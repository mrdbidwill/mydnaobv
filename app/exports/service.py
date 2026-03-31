from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
import zipfile

import httpx
from pypdf import PdfReader, PdfWriter
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.exports.config import export_config
from app.exports.publish import cleanup_published_job, is_latest_job_published, publish_enabled, publish_job_artifacts
from app.exports.pdf_writer import (
    render_empty_county_guide_pdf,
    render_observation_index_pdf,
    render_part_pdf,
)
from app.exports.policy import evaluate_license, normalize_license_code
from app.services.list_sync import sync_list_observations

ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_quota")
PICKABLE_JOB_STATUSES = ("queued", "waiting_quota")
FINISHED_JOB_STATUSES = ("ready", "partial_ready", "failed", "canceled")
AUTO_MAINTENANCE_CLEANUP_INTERVAL_HOURS = 24
GENUS_QUALIFIER_TOKENS = {
    "cf",
    "aff",
    "nr",
    "sp",
    "spp",
    "complex",
    "group",
    "sect",
    "subsp",
    "var",
    "forma",
}


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _resolved_sort_source(value: str | None = None) -> str:
    source = (value or settings.export_sort_taxon_source or "").strip().lower()
    if source in {"taxon", "inat_taxon"}:
        return "taxon"
    return "observation"


def _preferred_taxon_title(obs: models.Observation, sort_source: str | None = None) -> str:
    source = _resolved_sort_source(sort_source)
    if source == "taxon":
        ordered = (
            obs.taxon_name,
            obs.observation_taxon_name,
            obs.scientific_name,
            obs.community_taxon_name,
            obs.species_guess,
            obs.common_name,
        )
    else:
        ordered = (
            obs.observation_taxon_name,
            obs.scientific_name,
            obs.taxon_name,
            obs.community_taxon_name,
            obs.species_guess,
            obs.common_name,
        )
    for candidate in ordered:
        if candidate and candidate.strip():
            return candidate.strip()
    return ""


def _indexed_item_title(
    obs: models.Observation,
    observation_index: int,
    photo_suffix: str | None = None,
) -> str:
    base = _preferred_taxon_title(obs) or f"Observation {obs.inat_observation_id}"
    title = f"{observation_index}. {base}"
    if photo_suffix:
        return f"{title} ({photo_suffix})"
    return title


def _extract_genus_key(text: str) -> str:
    if not text:
        return ""
    for raw_token in text.split():
        token = re.sub(r"[^A-Za-z-]", "", raw_token).strip("-").lower()
        if not token:
            continue
        if token in GENUS_QUALIFIER_TOKENS:
            continue
        return token
    return ""


def _extract_genus_label(text: str) -> str:
    if not text:
        return ""
    for raw_token in text.split():
        cleaned = re.sub(r"[^A-Za-z-]", "", raw_token).strip("-")
        token = cleaned.lower()
        if not token:
            continue
        if token in GENUS_QUALIFIER_TOKENS:
            continue
        return cleaned if cleaned else token
    return ""


def _observation_genus_sort_key(
    obs: models.Observation,
    sort_source: str | None = None,
) -> tuple[str, str, int]:
    title = _preferred_taxon_title(obs, sort_source=sort_source)
    genus = _extract_genus_key(title)
    # Push no-genus rows to the end while keeping deterministic tie-breaks.
    genus_bucket = genus or "zzzzzzzz"
    return (
        genus_bucket,
        title.lower(),
        int(obs.inat_observation_id or 0),
    )


def _build_genera_count_lines(
    observations: list[models.Observation],
    sort_source: str | None = None,
) -> list[str]:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}

    for obs in observations:
        title = _preferred_taxon_title(obs, sort_source=sort_source)
        key = _extract_genus_key(title)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        if key not in labels:
            label = _extract_genus_label(title)
            labels[key] = label if label else key

    lines: list[str] = []
    for idx, key in enumerate(sorted(counts.keys()), start=1):
        label = labels.get(key, key)
        lines.append(f"{idx}. {label} ({counts[key]})")
    return lines


def enqueue_export_job(
    db: Session,
    list_id: int,
    requested_by: str | None = None,
    force_sync: bool = False,
) -> models.ExportJob:
    existing = (
        db.query(models.ExportJob)
        .filter(models.ExportJob.list_id == list_id, models.ExportJob.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(models.ExportJob.created_at.asc())
        .first()
    )
    if existing:
        return existing

    job = models.ExportJob(
        list_id=list_id,
        requested_by=requested_by,
        status="queued",
        phase="plan",
        force_sync=force_sync,
        part_size=max(10, export_config.part_size),
        next_run_at=utc_now_naive(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def latest_completed_job_for_list(db: Session, list_id: int) -> models.ExportJob | None:
    return (
        db.query(models.ExportJob)
        .filter(
            models.ExportJob.list_id == list_id,
            models.ExportJob.status.in_(("ready", "partial_ready")),
        )
        .order_by(models.ExportJob.finished_at.desc().nullslast(), models.ExportJob.id.desc())
        .first()
    )


def is_list_export_stale(
    obs_list: models.ObservationList,
    latest_job: models.ExportJob | None,
) -> tuple[bool, str]:
    if latest_job is None:
        return True, "No completed export exists yet."

    list_last_sync = normalize_naive_utc(obs_list.last_sync_at)
    if list_last_sync is None:
        return True, "List has not been synced yet since the last export."

    last_export_at = normalize_naive_utc(latest_job.finished_at) or normalize_naive_utc(latest_job.created_at)
    if last_export_at is None:
        return True, "Latest export has no completion timestamp; treat as stale."

    if list_last_sync > last_export_at:
        return (
            True,
            f"List data changed after the latest export ({list_last_sync} > {last_export_at}).",
        )
    return False, f"Latest export is up to date (last sync {list_last_sync}, export {last_export_at})."


def enqueue_export_job_for_list(
    db: Session,
    obs_list: models.ObservationList,
    requested_by: str | None = None,
    only_if_stale: bool = True,
    force_sync: bool = False,
) -> tuple[models.ExportJob, bool, str]:
    active = (
        db.query(models.ExportJob)
        .filter(models.ExportJob.list_id == obs_list.id, models.ExportJob.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(models.ExportJob.created_at.asc())
        .first()
    )
    if active:
        return active, False, f"Export job #{active.id} is already active ({active.status}/{active.phase})."

    latest = latest_completed_job_for_list(db, obs_list.id)
    if only_if_stale:
        stale, stale_reason = is_list_export_stale(obs_list, latest)
        if not stale and latest:
            return latest, False, f"No new job queued. {stale_reason}"

    job = models.ExportJob(
        list_id=obs_list.id,
        requested_by=requested_by,
        status="queued",
        phase="plan",
        force_sync=force_sync,
        part_size=max(10, export_config.part_size),
        next_run_at=utc_now_naive(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, True, f"Queued export job #{job.id}."


def enqueue_due_public_refresh_jobs(db: Session, limit: int = 2) -> int:
    """
    Queue stale public county/project lists for force-sync + rebuild.
    Runs in the worker loop to keep public refresh targets moving.
    """
    max_to_queue = max(1, min(limit, 25))
    now = utc_now_naive()
    cutoff = now - timedelta(days=max(1, settings.public_refresh_interval_days))

    active_rows = (
        db.query(models.ExportJob.list_id)
        .filter(models.ExportJob.status.in_(ACTIVE_JOB_STATUSES))
        .distinct()
        .all()
    )
    active_list_ids = {row[0] for row in active_rows if row[0] is not None}

    due_lists = (
        db.query(models.ObservationList)
        .filter(
            models.ObservationList.product_type.in_(("county", "project")),
            models.ObservationList.is_public_download.is_(True),
            or_(
                models.ObservationList.last_sync_at.is_(None),
                models.ObservationList.last_sync_at <= cutoff,
            ),
        )
        .order_by(models.ObservationList.last_sync_at.asc().nullsfirst(), models.ObservationList.id.asc())
        .all()
    )

    queued = 0
    for obs_list in due_lists:
        if obs_list.id in active_list_ids:
            continue
        _, was_queued, _ = enqueue_export_job_for_list(
            db,
            obs_list,
            requested_by="auto-refresh",
            only_if_stale=False,
            force_sync=True,
        )
        if was_queued:
            queued += 1
            active_list_ids.add(obs_list.id)
        if queued >= max_to_queue:
            break
    return queued


def enqueue_due_public_county_refresh_jobs(db: Session, limit: int = 2) -> int:
    # Backward-compatible name kept for older call sites/tests.
    return enqueue_due_public_refresh_jobs(db, limit=limit)


def list_jobs_for_list(db: Session, list_id: int, limit: int = 10) -> list[models.ExportJob]:
    return (
        db.query(models.ExportJob)
        .filter(models.ExportJob.list_id == list_id)
        .order_by(models.ExportJob.created_at.desc())
        .limit(limit)
        .all()
    )


def list_artifacts_for_job(db: Session, job_id: int) -> list[models.ExportArtifact]:
    return (
        db.query(models.ExportArtifact)
        .filter(models.ExportArtifact.job_id == job_id)
        .order_by(models.ExportArtifact.kind.asc(), models.ExportArtifact.part_number.asc().nullslast())
        .all()
    )


def get_artifact_for_job(db: Session, job_id: int, artifact_id: int) -> models.ExportArtifact | None:
    return (
        db.query(models.ExportArtifact)
        .filter(models.ExportArtifact.job_id == job_id, models.ExportArtifact.id == artifact_id)
        .first()
    )


def artifact_abspath(artifact: models.ExportArtifact) -> Path:
    return _storage_root() / artifact.relative_path


def process_next_job(db: Session) -> models.ExportJob | None:
    if not export_config.enabled:
        return None

    now = utc_now_naive()
    job = _pick_next_job(db, now)
    if not job:
        return None

    if job.status in PICKABLE_JOB_STATUSES:
        job.status = "running"
    if not job.started_at:
        job.started_at = now
    job.next_run_at = None
    job.updated_at = utc_now_naive()
    db.commit()

    deadline = time.monotonic() + max(5, export_config.run_timeout_seconds)

    try:
        while time.monotonic() < deadline:
            progressed = _process_phase(db, job, deadline)
            _refresh_job_counts(db, job)
            job.updated_at = utc_now_naive()
            db.commit()

            if job.status in FINISHED_JOB_STATUSES:
                return job
            if not progressed:
                break

        if job.status not in FINISHED_JOB_STATUSES:
            _schedule_next_run(job, utc_now_naive())
            if job.status == "running":
                # Release claim so the next worker cycle can continue the job.
                job.status = "queued"
            job.updated_at = utc_now_naive()
            db.commit()

        return job
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        failed_job = db.query(models.ExportJob).filter(models.ExportJob.id == job.id).first()
        if failed_job:
            failed_job.status = "failed"
            failed_job.phase = "done"
            failed_job.message = f"worker_error: {exc}"
            failed_job.finished_at = utc_now_naive()
            failed_job.updated_at = utc_now_naive()
            db.commit()
            return failed_job
        return job


def cleanup_expired_exports(db: Session) -> int:
    now = utc_now_naive()
    cutoff = now - timedelta(hours=max(1, export_config.retention_hours))
    jobs = (
        db.query(models.ExportJob)
        .filter(models.ExportJob.finished_at.isnot(None), models.ExportJob.finished_at < cutoff)
        .all()
    )

    removed = 0
    for job in jobs:
        folder = _job_dir(job.id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        cleanup_published_job(job.list_id, job.id)
        removed += 1
    if removed:
        db.commit()
    return removed


def process_pending_publish_jobs(db: Session, limit: int | None = None) -> int:
    if not publish_enabled():
        return 0

    max_to_publish = max(1, min(limit or export_config.publish_jobs_per_run, 10))
    candidates = (
        db.query(models.ExportJob)
        .join(models.ObservationList, models.ObservationList.id == models.ExportJob.list_id)
        .filter(
            models.ExportJob.status.in_(("ready", "partial_ready")),
            models.ObservationList.is_public_download.is_(True),
            models.ObservationList.product_type.in_(("county", "project")),
        )
        .order_by(models.ExportJob.finished_at.asc().nullslast(), models.ExportJob.id.asc())
        .limit(200)
        .all()
    )

    published_count = 0
    storage_root = _storage_root()
    for job in candidates:
        if is_latest_job_published(job.list_id, job.id):
            continue

        artifacts = list_artifacts_for_job(db, job.id)
        if not artifacts:
            continue

        publish_warning = publish_job_artifacts(job, artifacts, storage_root)
        if publish_warning:
            job.message = _append_job_note(job.message, f"Publish note: {publish_warning}")
        else:
            job.message = _append_job_note(job.message, "Publish complete.")
        job.updated_at = utc_now_naive()
        db.commit()

        published_count += 1
        if published_count >= max_to_publish:
            break

    return published_count


def run_scheduled_maintenance(db: Session) -> dict[str, int]:
    """
    Lightweight recurring maintenance intended for frequent worker runs.
    Uses a state file to avoid heavy work on every pass.
    """
    now = utc_now_naive()
    state = _load_auto_maintenance_state()
    removed_jobs = 0
    pruned_cache_files = 0

    cleanup_cutoff = now - timedelta(hours=AUTO_MAINTENANCE_CLEANUP_INTERVAL_HOURS)
    last_cleanup = _parse_naive_utc(state.get("last_cleanup_at"))
    if last_cleanup is None or last_cleanup <= cleanup_cutoff:
        removed_jobs = cleanup_expired_exports(db)
        state["last_cleanup_at"] = now.isoformat()

    prune_interval = max(1, export_config.image_cache_prune_interval_hours)
    prune_cutoff = now - timedelta(hours=prune_interval)
    last_prune = _parse_naive_utc(state.get("last_image_cache_prune_at"))
    if last_prune is None or last_prune <= prune_cutoff:
        pruned_cache_files = prune_image_cache(
            now=now,
            max_files=max(1, export_config.image_cache_max_prune_files),
        )
        state["last_image_cache_prune_at"] = now.isoformat()

    _save_auto_maintenance_state(state)
    return {
        "removed_jobs": removed_jobs,
        "pruned_cache_files": pruned_cache_files,
    }


def _pick_next_job(db: Session, now: datetime) -> models.ExportJob | None:
    _requeue_stale_running_jobs(db, now)

    bucket_rank = case(
        (models.ExportJob.size_bucket == "XS", 0),
        (models.ExportJob.size_bucket == "S", 1),
        (models.ExportJob.size_bucket == "M", 2),
        (models.ExportJob.size_bucket == "L", 3),
        else_=0,
    )

    candidates = (
        db.query(models.ExportJob)
        .filter(
            models.ExportJob.status.in_(PICKABLE_JOB_STATUSES),
            (models.ExportJob.next_run_at.is_(None) | (models.ExportJob.next_run_at <= now)),
        )
        .order_by(bucket_rank.asc(), models.ExportJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(25)
        .all()
    )

    for job in candidates:
        if job.size_bucket == "L" and not export_config.is_large_window_open(now):
            job.next_run_at = export_config.next_large_window_start(now)
            job.updated_at = utc_now_naive()
            continue
        return job

    if candidates:
        db.commit()
    return None


def _requeue_stale_running_jobs(db: Session, now: datetime) -> int:
    """
    Recover jobs stuck in `running` due to worker interruption.
    """
    stale_after_seconds = max(120, export_config.run_timeout_seconds * 3)
    stale_cutoff = now - timedelta(seconds=stale_after_seconds)
    stale_jobs = (
        db.query(models.ExportJob)
        .filter(
            models.ExportJob.status == "running",
            models.ExportJob.updated_at.isnot(None),
            models.ExportJob.updated_at <= stale_cutoff,
        )
        .all()
    )
    if not stale_jobs:
        return 0
    for job in stale_jobs:
        job.status = "queued"
        job.next_run_at = now
        job.updated_at = utc_now_naive()
        message = (job.message or "").strip()
        recovery_note = "Recovered stale running job lock."
        if recovery_note not in message:
            job.message = f"{message} {recovery_note}".strip()
    db.commit()
    return len(stale_jobs)


def _process_phase(db: Session, job: models.ExportJob, deadline: float) -> bool:
    if job.phase == "plan":
        return _phase_plan(db, job)
    if job.phase == "download":
        return _phase_download(db, job, deadline)
    if job.phase == "render":
        return _phase_render(db, job)
    if job.phase == "finalize":
        return _phase_finalize(db, job)
    if job.phase == "done":
        if job.status not in FINISHED_JOB_STATUSES:
            job.status = "ready"
            job.finished_at = utc_now_naive()
        return False

    job.phase = "plan"
    return True


def _phase_plan(db: Session, job: models.ExportJob) -> bool:
    existing_count = db.query(func.count(models.ExportItem.id)).filter(models.ExportItem.job_id == job.id).scalar() or 0
    if existing_count > 0:
        job.phase = "download"
        return True

    if job.force_sync:
        obs_list = db.query(models.ObservationList).filter(models.ObservationList.id == job.list_id).first()
        if not obs_list:
            job.status = "failed"
            job.phase = "done"
            job.message = "List not found for sync."
            job.finished_at = utc_now_naive()
            return True
        try:
            synced = sync_list_observations(db, obs_list)
            job.message = f"Sync complete: {synced} observations refreshed."
            job.force_sync = False
        except Exception as exc:
            db.rollback()
            throttle_delay_seconds = _sync_throttle_delay_seconds(exc)
            if throttle_delay_seconds is not None:
                now = utc_now_naive()
                retry_at = now + timedelta(seconds=throttle_delay_seconds)
                job.status = "waiting_quota"
                job.phase = "plan"
                job.next_run_at = retry_at
                job.last_run_at = now
                job.message = (
                    "Sync paused by iNaturalist throttling (HTTP 429). "
                    f"Retry after {throttle_delay_seconds}s at {retry_at.isoformat()} UTC."
                )
                return False
            job.status = "failed"
            job.phase = "done"
            job.message = f"Sync failed before export: {exc}"
            job.finished_at = utc_now_naive()
            return True

    observations = (
        db.query(models.Observation)
        .filter(models.Observation.list_id == job.list_id)
        .all()
    )
    observations.sort(key=_observation_genus_sort_key)
    observation_index_by_id = {obs.id: idx for idx, obs in enumerate(observations, start=1)}
    observation_ids = [obs.id for obs in observations]
    photos_by_observation: dict[int, list[models.ObservationPhoto]] = {}
    if observation_ids:
        photo_rows = (
            db.query(models.ObservationPhoto)
            .filter(models.ObservationPhoto.observation_id.in_(observation_ids))
            .order_by(
                models.ObservationPhoto.observation_id.asc(),
                models.ObservationPhoto.photo_index.asc(),
                models.ObservationPhoto.id.asc(),
            )
            .all()
        )
        for photo in photo_rows:
            photos_by_observation.setdefault(photo.observation_id, []).append(photo)

    sequence = 1
    for obs in observations:
        observation_index = observation_index_by_id.get(obs.id, sequence)
        candidates = _photo_candidates_for_observation(
            obs,
            photos=photos_by_observation.get(obs.id, []),
        )
        if not candidates:
            db.add(
                models.ExportItem(
                    job_id=job.id,
                    observation_id=obs.id,
                    sequence=sequence,
                    inat_observation_id=obs.inat_observation_id,
                    item_title=_indexed_item_title(obs, observation_index),
                    observation_taxon_name=obs.observation_taxon_name or obs.scientific_name,
                    community_taxon_name=obs.community_taxon_name,
                    barcode_inferred_species_or_name=obs.barcode_inferred_species_or_name,
                    observed_at=obs.observed_at,
                    inat_url=obs.inat_url,
                    image_url=None,
                    image_license_code=None,
                    image_attribution=None,
                    status="skipped",
                    skip_reason="no_image_url",
                )
            )
            sequence += 1
            continue

        total_candidates = len(candidates)
        for idx, candidate in enumerate(candidates, start=1):
            decision = evaluate_license(candidate["license_code"])
            item_status = "pending" if decision.allowed else "skipped"
            skip_reason = None if decision.allowed else f"license:{decision.reason}"
            title = _indexed_item_title(obs, observation_index)
            if export_config.include_all_photos and total_candidates > 1:
                title = _indexed_item_title(obs, observation_index, f"photo {idx}/{total_candidates}")

            db.add(
                models.ExportItem(
                    job_id=job.id,
                    observation_id=obs.id,
                    sequence=sequence,
                    inat_observation_id=obs.inat_observation_id,
                    item_title=title,
                    observation_taxon_name=obs.observation_taxon_name or obs.scientific_name,
                    community_taxon_name=obs.community_taxon_name,
                    barcode_inferred_species_or_name=obs.barcode_inferred_species_or_name,
                    observed_at=obs.observed_at,
                    inat_url=obs.inat_url,
                    image_url=candidate["url"],
                    image_license_code=normalize_license_code(candidate["license_code"]),
                    image_attribution=candidate["attribution"],
                    status=item_status,
                    skip_reason=skip_reason,
                )
            )
            sequence += 1

    db.flush()
    job.total_items = max(0, sequence - 1)
    eligible = (
        db.query(func.count(models.ExportItem.id))
        .filter(models.ExportItem.job_id == job.id, models.ExportItem.status == "pending")
        .scalar()
        or 0
    )
    job.eligible_items = eligible
    job.size_bucket = export_config.classify_bucket(eligible)
    job.part_size = _recommended_part_size()

    if eligible == 0:
        # Complete gracefully so each county still publishes the two expected documents.
        job.phase = "finalize"
        job.next_run_at = utc_now_naive()
        job.message = (
            "No exportable county guide pages were eligible "
            "(missing images and/or excluded by license policy). "
            "Generating observations index and placeholder county guide."
        )
        return True

    job.phase = "download"
    job.next_run_at = utc_now_naive()
    _job_dir(job.id).mkdir(parents=True, exist_ok=True)
    return True


def _phase_download(db: Session, job: models.ExportJob, deadline: float) -> bool:
    quota = _load_quota_state()
    now = utc_now_naive()
    _reset_quota_windows(quota, now)

    pending = (
        db.query(models.ExportItem)
        .filter(models.ExportItem.job_id == job.id, models.ExportItem.status == "pending")
        .order_by(models.ExportItem.sequence.asc())
        .limit(_effective_download_chunk_size())
        .all()
    )
    if not pending:
        job.phase = "render"
        return True

    progressed = False
    run_byte_budget_mb = max(1, export_config.download_byte_budget_mb)
    if export_config.include_all_photos:
        run_byte_budget_mb = min(run_byte_budget_mb, 40)
    run_byte_budget = run_byte_budget_mb * 1024 * 1024
    run_bytes = 0

    images_dir = _job_dir(job.id) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=True) as client:
        for item in pending:
            if time.monotonic() >= deadline:
                break

            cached_path, cache_is_fresh = _lookup_image_cache_path(item.image_url or "", now)
            if cached_path and cache_is_fresh:
                try:
                    item.local_image_relpath = _materialize_item_image_from_cache(job.id, item.id, cached_path)
                    item.status = "downloaded"
                    item.error_message = None
                    item.updated_at = utc_now_naive()
                    progressed = True
                    continue
                except Exception:
                    # Fall through to a live fetch when cache copy fails.
                    pass

            if quota["day_requests"] >= export_config.max_api_requests_per_day:
                if cached_path:
                    try:
                        item.local_image_relpath = _materialize_item_image_from_cache(job.id, item.id, cached_path)
                        item.status = "downloaded"
                        item.error_message = "used_cached_image_due_to_api_request_quota"
                        item.updated_at = utc_now_naive()
                        progressed = True
                        continue
                    except Exception:
                        pass
                job.status = "waiting_quota"
                job.message = "Paused: reached daily API request budget."
                job.next_run_at = now + timedelta(hours=1)
                _save_quota_state(quota)
                return False

            if quota["day_bytes"] >= export_config.max_media_mb_per_day * 1024 * 1024:
                if cached_path:
                    try:
                        item.local_image_relpath = _materialize_item_image_from_cache(job.id, item.id, cached_path)
                        item.status = "downloaded"
                        item.error_message = "used_cached_image_due_to_daily_media_quota"
                        item.updated_at = utc_now_naive()
                        progressed = True
                        continue
                    except Exception:
                        pass
                job.status = "waiting_quota"
                job.message = "Paused: reached daily media download budget."
                job.next_run_at = now + timedelta(hours=1)
                _save_quota_state(quota)
                return False

            if quota["hour_bytes"] >= export_config.max_media_mb_per_hour * 1024 * 1024:
                if cached_path:
                    try:
                        item.local_image_relpath = _materialize_item_image_from_cache(job.id, item.id, cached_path)
                        item.status = "downloaded"
                        item.error_message = "used_cached_image_due_to_hourly_media_quota"
                        item.updated_at = utc_now_naive()
                        progressed = True
                        continue
                    except Exception:
                        pass
                job.status = "waiting_quota"
                job.message = "Paused: reached hourly media download budget."
                job.next_run_at = now + timedelta(hours=1)
                _save_quota_state(quota)
                return False

            if run_bytes >= run_byte_budget:
                if cached_path:
                    try:
                        item.local_image_relpath = _materialize_item_image_from_cache(job.id, item.id, cached_path)
                        item.status = "downloaded"
                        item.error_message = "used_cached_image_due_to_run_byte_budget"
                        item.updated_at = utc_now_naive()
                        progressed = True
                        continue
                    except Exception:
                        pass
                break

            item.attempts += 1
            quota["day_requests"] += 1
            job.api_requests += 1

            try:
                response = client.get(item.image_url or "")
                response.raise_for_status()
                payload = response.content
                content_type = (response.headers.get("content-type") or "").lower()
                if "image" not in content_type:
                    raise ValueError(f"non-image content-type: {content_type}")

                payload_size = len(payload)
                if run_bytes + payload_size > run_byte_budget:
                    break

                relative_path = _materialize_item_image_from_payload(
                    job.id,
                    item.id,
                    payload,
                    content_type,
                )
                _store_image_cache_entry(
                    image_url=item.image_url or "",
                    payload=payload,
                    content_type=content_type,
                    now=now,
                )

                item.local_image_relpath = relative_path
                item.status = "downloaded"
                item.error_message = None
                item.updated_at = utc_now_naive()

                run_bytes += payload_size
                quota["day_bytes"] += payload_size
                quota["hour_bytes"] += payload_size
                job.bytes_downloaded += payload_size
                progressed = True
            except Exception as exc:
                if cached_path:
                    try:
                        item.local_image_relpath = _materialize_item_image_from_cache(job.id, item.id, cached_path)
                        item.status = "downloaded"
                        item.error_message = f"refresh_failed_used_cached_image: {exc}"
                        item.updated_at = utc_now_naive()
                        progressed = True
                        continue
                    except Exception:
                        pass

                item.status = "failed" if item.attempts >= 3 else "pending"
                item.error_message = str(exc)
                item.updated_at = utc_now_naive()
                if item.status == "failed":
                    item.skip_reason = "download_failed"
            finally:
                if export_config.request_interval_seconds > 0:
                    time.sleep(export_config.request_interval_seconds)

    _save_quota_state(quota)

    db.flush()
    remaining_pending = (
        db.query(func.count(models.ExportItem.id))
        .filter(models.ExportItem.job_id == job.id, models.ExportItem.status == "pending")
        .scalar()
        or 0
    )

    if remaining_pending == 0:
        job.phase = "render"
    job.status = "running"
    return progressed


def _phase_render(db: Session, job: models.ExportJob) -> bool:
    downloaded_items = (
        db.query(models.ExportItem)
        .filter(models.ExportItem.job_id == job.id, models.ExportItem.status == "downloaded")
        .order_by(models.ExportItem.sequence.asc())
        .limit(max(10, job.part_size))
        .all()
    )

    if not downloaded_items:
        pending_count = (
            db.query(func.count(models.ExportItem.id))
            .filter(models.ExportItem.job_id == job.id, models.ExportItem.status == "pending")
            .scalar()
            or 0
        )
        if pending_count > 0:
            job.phase = "download"
        else:
            added = _add_placeholder_items_for_uncovered_observations(db, job)
            if added > 0:
                return True
            job.phase = "finalize"
        return True

    next_part = (
        (db.query(func.max(models.ExportArtifact.part_number))
         .filter(models.ExportArtifact.job_id == job.id, models.ExportArtifact.kind == "part_pdf")
         .scalar())
        or 0
    ) + 1

    part_relpath = f"parts/part_{next_part:03d}.pdf"
    part_abspath = _job_dir(job.id) / part_relpath

    render_part_pdf(part_abspath, downloaded_items, _job_dir(job.id))

    artifact = models.ExportArtifact(
        job_id=job.id,
        kind="part_pdf",
        part_number=next_part,
        relative_path=_relative_to_storage(part_abspath),
        size_bytes=part_abspath.stat().st_size,
    )
    db.add(artifact)

    for item in downloaded_items:
        item.status = "rendered"
        item.part_number = next_part
        item.updated_at = utc_now_naive()

    return True


def _add_placeholder_items_for_uncovered_observations(db: Session, job: models.ExportJob) -> int:
    observations = (
        db.query(models.Observation)
        .filter(models.Observation.list_id == job.list_id)
        .all()
    )
    if not observations:
        return 0

    observations.sort(key=_observation_genus_sort_key)
    observation_index_by_id = {obs.id: idx for idx, obs in enumerate(observations, start=1)}

    covered_rows = (
        db.query(models.ExportItem.observation_id)
        .filter(
            models.ExportItem.job_id == job.id,
            models.ExportItem.observation_id.isnot(None),
            models.ExportItem.status.in_(("pending", "downloaded", "rendered")),
        )
        .distinct()
        .all()
    )
    covered_observation_ids = {row[0] for row in covered_rows if row[0] is not None}

    max_sequence = (
        db.query(func.max(models.ExportItem.sequence))
        .filter(models.ExportItem.job_id == job.id)
        .scalar()
        or 0
    )
    added = 0

    for obs in observations:
        if obs.id in covered_observation_ids:
            continue
        max_sequence += 1
        observation_index = observation_index_by_id.get(obs.id, max_sequence)
        added += 1
        db.add(
            models.ExportItem(
                job_id=job.id,
                observation_id=obs.id,
                sequence=max_sequence,
                inat_observation_id=obs.inat_observation_id,
                item_title=_indexed_item_title(obs, observation_index, "image unavailable in this build"),
                observation_taxon_name=obs.observation_taxon_name or obs.scientific_name,
                community_taxon_name=obs.community_taxon_name,
                barcode_inferred_species_or_name=obs.barcode_inferred_species_or_name,
                observed_at=obs.observed_at,
                inat_url=obs.inat_url,
                image_url=None,
                image_license_code=None,
                image_attribution=None,
                status="downloaded",
                skip_reason="placeholder:image_unavailable_in_build",
            )
        )

    return added


def _phase_finalize(db: Session, job: models.ExportJob) -> bool:
    parts = (
        db.query(models.ExportArtifact)
        .filter(models.ExportArtifact.job_id == job.id, models.ExportArtifact.kind == "part_pdf")
        .order_by(models.ExportArtifact.part_number.asc())
        .all()
    )

    docs_dir = _job_dir(job.id) / "final"
    docs_dir.mkdir(parents=True, exist_ok=True)

    readme_path = docs_dir / "README_FIRST.txt"
    readme_path.write_text(_build_readme_text(job), encoding="utf-8")
    _upsert_artifact(db, job.id, "readme", _relative_to_storage(readme_path), None)

    obs_list = db.query(models.ObservationList).filter(models.ObservationList.id == job.list_id).first()
    filename_prefix = _filename_prefix_for_list(obs_list, job.list_id)
    index_pdf_name = f"{filename_prefix}_observations_index.pdf"
    county_pdf_name = f"{filename_prefix}_all_observations.pdf"
    genera_count_name = f"{filename_prefix}_genera_count.txt"
    zip_name = f"{filename_prefix}_observation_export_parts.zip"
    observations = (
        db.query(models.Observation)
        .filter(models.Observation.list_id == job.list_id)
        .all()
    )
    observations.sort(key=_observation_genus_sort_key)
    observation_index_by_id = {obs.id: idx for idx, obs in enumerate(observations, start=1)}

    rendered_observation_rows = (
        db.query(models.ExportItem.observation_id)
        .filter(
            models.ExportItem.job_id == job.id,
            models.ExportItem.observation_id.isnot(None),
            models.ExportItem.status == "rendered",
        )
        .distinct()
        .all()
    )
    rendered_observation_ids = {row[0] for row in rendered_observation_rows if row[0] is not None}
    missing_observations = [obs for obs in observations if obs.id not in rendered_observation_ids]
    placeholder_pages_added = 0

    if missing_observations:
        next_part = (
            (db.query(func.max(models.ExportArtifact.part_number))
             .filter(models.ExportArtifact.job_id == job.id, models.ExportArtifact.kind == "part_pdf")
             .scalar())
            or 0
        ) + 1
        max_sequence = (
            db.query(func.max(models.ExportItem.sequence))
            .filter(models.ExportItem.job_id == job.id)
            .scalar()
            or 0
        )
        placeholder_items: list[models.ExportItem] = []
        for obs in missing_observations:
            max_sequence += 1
            placeholder_pages_added += 1
            observation_index = observation_index_by_id.get(obs.id, max_sequence)
            item = models.ExportItem(
                job_id=job.id,
                observation_id=obs.id,
                sequence=max_sequence,
                inat_observation_id=obs.inat_observation_id,
                item_title=_indexed_item_title(obs, observation_index, "image unavailable in this build"),
                observation_taxon_name=obs.observation_taxon_name or obs.scientific_name,
                community_taxon_name=obs.community_taxon_name,
                barcode_inferred_species_or_name=obs.barcode_inferred_species_or_name,
                observed_at=obs.observed_at,
                inat_url=obs.inat_url,
                image_url=None,
                image_license_code=None,
                image_attribution=None,
                status="rendered",
                part_number=next_part,
                skip_reason="placeholder:image_unavailable_in_build",
            )
            db.add(item)
            placeholder_items.append(item)
        db.flush()

        part_relpath = f"parts/part_{next_part:03d}.pdf"
        part_abspath = _job_dir(job.id) / part_relpath
        render_part_pdf(part_abspath, placeholder_items, _job_dir(job.id))
        db.add(
            models.ExportArtifact(
                job_id=job.id,
                kind="part_pdf",
                part_number=next_part,
                relative_path=_relative_to_storage(part_abspath),
                size_bytes=part_abspath.stat().st_size,
            )
        )
        db.flush()

        parts = (
            db.query(models.ExportArtifact)
            .filter(models.ExportArtifact.job_id == job.id, models.ExportArtifact.kind == "part_pdf")
            .order_by(models.ExportArtifact.part_number.asc())
            .all()
        )

    index_pdf_path = docs_dir / index_pdf_name
    render_observation_index_pdf(
        output_path=index_pdf_path,
        list_title=obs_list.title if obs_list else f"List {job.list_id}",
        observations=observations,
    )
    _upsert_artifact(db, job.id, "observations_index_pdf", _relative_to_storage(index_pdf_path), None)

    genera_count_path = docs_dir / genera_count_name
    genera_lines = _build_genera_count_lines(observations)
    genera_count_text = "\n".join(genera_lines) + ("\n" if genera_lines else "")
    genera_count_path.write_text(genera_count_text, encoding="utf-8")
    _upsert_artifact(db, job.id, "genera_count", _relative_to_storage(genera_count_path), None)

    merged_created = False
    used_placeholder_county_guide = False
    if not parts:
        merged_path = docs_dir / county_pdf_name
        render_empty_county_guide_pdf(
            output_path=merged_path,
            list_title=obs_list.title if obs_list else f"List {job.list_id}",
            reason=(
                "No exportable county guide pages were available "
                "(images missing, restricted by license, or download unavailable)."
            ),
        )
        _upsert_artifact(db, job.id, "merged_pdf", _relative_to_storage(merged_path), None)
        merged_created = True
        used_placeholder_county_guide = True
    elif len(parts) <= max(1, export_config.zip_only_part_threshold):
        merged_path = docs_dir / county_pdf_name
        writer = PdfWriter()
        for part in parts:
            part_path = _storage_root() / part.relative_path
            reader = PdfReader(str(part_path))
            for page in reader.pages:
                writer.add_page(page)
        with merged_path.open("wb") as merged_file:
            writer.write(merged_file)
        _upsert_artifact(db, job.id, "merged_pdf", _relative_to_storage(merged_path), None)
        merged_created = True

    zip_path = docs_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(
            readme_path,
            arcname="README_FIRST.txt",
            compress_type=_zip_compression_for_arcname("README_FIRST.txt"),
        )
        zf.write(
            index_pdf_path,
            arcname=index_pdf_name,
            compress_type=_zip_compression_for_arcname(index_pdf_name),
        )
        zf.write(
            genera_count_path,
            arcname=genera_count_name,
            compress_type=_zip_compression_for_arcname(genera_count_name),
        )
        for part in parts:
            part_path = _storage_root() / part.relative_path
            arcname = f"parts/{Path(part.relative_path).name}"
            zf.write(part_path, arcname=arcname, compress_type=_zip_compression_for_arcname(arcname))
        if merged_created:
            merged_path = docs_dir / county_pdf_name
            zf.write(
                merged_path,
                arcname=county_pdf_name,
                compress_type=_zip_compression_for_arcname(county_pdf_name),
            )

    _upsert_artifact(db, job.id, "zip", _relative_to_storage(zip_path), None)
    chunk_paths = _split_large_zip(zip_path, export_config.zip_chunk_size_mb)
    _replace_zip_chunk_artifacts(db, job.id, chunk_paths)

    job.phase = "done"
    job.status = "ready" if merged_created else "partial_ready"
    job.finished_at = utc_now_naive()
    if used_placeholder_county_guide:
        job.message = "Export complete: placeholder county guide PDF, observations index PDF, and ZIP ready."
    else:
        job.message = (
            "Export complete: county guide PDF, observations index PDF, and ZIP ready."
            if merged_created
            else "Export complete: observations index PDF and ZIP with split county guide parts ready."
        )
    if placeholder_pages_added > 0:
        job.message = (
            f"{job.message} Included {placeholder_pages_added} observation placeholder page(s) "
            "where images were unavailable in this build."
        )
    if publish_enabled():
        job.message = _append_job_note(job.message, "Publish queued.")
    job.next_run_at = None
    return True


def _refresh_job_counts(db: Session, job: models.ExportJob) -> None:
    db.flush()
    counts = dict(
        db.query(models.ExportItem.status, func.count(models.ExportItem.id))
        .filter(models.ExportItem.job_id == job.id)
        .group_by(models.ExportItem.status)
        .all()
    )
    job.total_items = sum(counts.values())
    job.eligible_items = counts.get("pending", 0) + counts.get("downloaded", 0) + counts.get("rendered", 0)
    job.downloaded_items = counts.get("downloaded", 0)
    job.rendered_items = counts.get("rendered", 0)
    job.skipped_items = counts.get("skipped", 0)
    job.failed_items = counts.get("failed", 0)


def _schedule_next_run(job: models.ExportJob, now: datetime) -> None:
    if job.status == "waiting_quota" and job.next_run_at and job.next_run_at > now:
        job.last_run_at = now
        return
    bucket = job.size_bucket or "L"
    if bucket == "L" and not export_config.is_large_window_open(now):
        job.next_run_at = export_config.next_large_window_start(now)
        job.status = "waiting_quota"
        return
    job.next_run_at = now + export_config.cadence_for_bucket(bucket)
    job.last_run_at = now


def _recommended_part_size() -> int:
    # KVM 1 defaults: keep parts conservative to avoid merge pressure.
    if export_config.include_all_photos:
        return max(30, min(export_config.part_size, 75))
    return max(50, min(export_config.part_size, 150))


def _zip_compression_for_arcname(arcname: str) -> int:
    lowered = (arcname or "").strip().lower()
    if lowered.endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".zip")):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _split_large_zip(zip_path: Path, chunk_size_mb: int) -> list[Path]:
    size_bytes = zip_path.stat().st_size if zip_path.exists() else 0
    chunk_size_bytes = max(0, int(chunk_size_mb)) * 1024 * 1024
    if size_bytes <= 0 or chunk_size_bytes <= 0 or size_bytes <= chunk_size_bytes:
        return []

    chunk_dir = zip_path.parent / "chunks"
    if chunk_dir.exists():
        shutil.rmtree(chunk_dir, ignore_errors=True)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    chunk_paths: list[Path] = []
    chunk_index = 1
    with zip_path.open("rb") as source:
        while True:
            chunk_name = f"{zip_path.name}.part{chunk_index:03d}"
            chunk_path = chunk_dir / chunk_name
            bytes_written = 0
            with chunk_path.open("wb") as destination:
                while bytes_written < chunk_size_bytes:
                    to_read = min(4 * 1024 * 1024, chunk_size_bytes - bytes_written)
                    payload = source.read(to_read)
                    if not payload:
                        break
                    destination.write(payload)
                    bytes_written += len(payload)

            if bytes_written == 0:
                chunk_path.unlink(missing_ok=True)
                break

            chunk_paths.append(chunk_path)
            chunk_index += 1

            if bytes_written < chunk_size_bytes:
                break

    return chunk_paths


def _replace_zip_chunk_artifacts(db: Session, job_id: int, chunk_paths: list[Path]) -> None:
    existing = (
        db.query(models.ExportArtifact)
        .filter(models.ExportArtifact.job_id == job_id, models.ExportArtifact.kind == "zip_chunk")
        .all()
    )
    for artifact in existing:
        abs_path = _storage_root() / artifact.relative_path
        if abs_path.exists():
            abs_path.unlink(missing_ok=True)
        db.delete(artifact)
    db.flush()

    for idx, chunk_path in enumerate(chunk_paths, start=1):
        db.add(
            models.ExportArtifact(
                job_id=job_id,
                kind="zip_chunk",
                part_number=idx,
                relative_path=_relative_to_storage(chunk_path),
                size_bytes=chunk_path.stat().st_size if chunk_path.exists() else 0,
            )
        )


def _append_job_note(message: str | None, note: str) -> str:
    clean_note = (note or "").strip()
    if not clean_note:
        return (message or "").strip()

    current = (message or "").strip()
    if clean_note in current:
        return current
    if not current:
        return clean_note
    return f"{current} {clean_note}"


def _sync_throttle_delay_seconds(exc: Exception) -> int | None:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None

    response = exc.response
    if response is None or response.status_code != 429:
        return None

    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
    if retry_after is None:
        return 30 * 60
    return max(60, min(retry_after, 6 * 60 * 60))


def _retry_after_seconds(raw_value: str | None) -> int | None:
    value = (raw_value or "").strip()
    if not value:
        return None

    if value.isdigit():
        return max(0, int(value))

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    return max(0, int((parsed.astimezone(UTC) - now).total_seconds()))


def _effective_download_chunk_size() -> int:
    chunk_size = max(1, export_config.download_chunk_size)
    if export_config.include_all_photos:
        return min(chunk_size, 4)
    return chunk_size


def _photo_candidates_for_observation(
    obs: models.Observation,
    photos: list[models.ObservationPhoto] | None = None,
) -> list[dict[str, str | None]]:
    photo_rows = _coerce_photo_collection(photos if photos is not None else getattr(obs, "photos", None))

    if export_config.include_all_photos:
        max_per_obs = max(1, min(export_config.max_photos_per_observation, 8))
        ordered = sorted(photo_rows, key=lambda p: (p.photo_index, p.id))
        selected = ordered[:max_per_obs]
        if selected:
            return [
                {
                    "url": photo.photo_url,
                    "license_code": photo.photo_license_code,
                    "attribution": photo.photo_attribution,
                }
                for photo in selected
                if photo.photo_url
            ]

    # Default behavior: export one primary image per observation.
    if obs.photo_url:
        return [
            {
                "url": obs.photo_url,
                "license_code": obs.photo_license_code,
                "attribution": obs.photo_attribution,
            }
        ]

    # Fallback to first cached photo entry if primary is missing.
    if photo_rows:
        first = sorted(photo_rows, key=lambda p: (p.photo_index, p.id))[0]
        if first.photo_url:
            return [
                {
                    "url": first.photo_url,
                    "license_code": first.photo_license_code,
                    "attribution": first.photo_attribution,
                }
            ]
    return []


def _coerce_photo_collection(raw: object) -> list[models.ObservationPhoto]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [photo for photo in raw if photo is not None]
    try:
        return [photo for photo in list(raw) if photo is not None]
    except TypeError:
        return [raw] if raw is not None else []


def _storage_root() -> Path:
    root = Path(export_config.storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _parse_naive_utc(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _auto_maintenance_state_path() -> Path:
    return _storage_root() / "maintenance_state.json"


def _load_auto_maintenance_state() -> dict[str, str]:
    path = _auto_maintenance_state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("last_cleanup_at", "last_image_cache_prune_at"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value
    return out


def _save_auto_maintenance_state(payload: dict[str, str]) -> None:
    path = _auto_maintenance_state_path()
    path.write_text(json.dumps(payload), encoding="utf-8")


def _image_cache_enabled() -> bool:
    return bool(export_config.image_cache_enabled)


def _image_cache_root() -> Path:
    root = _storage_root() / "image_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _image_cache_key(image_url: str) -> str:
    cleaned = str(image_url or "").strip()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def _image_cache_meta_path(image_url: str) -> Path:
    key = _image_cache_key(image_url)
    return _image_cache_root() / key[:2] / f"{key}.json"


def _image_cache_payload_path_from_meta(meta_path: Path, meta: dict[str, object]) -> Path | None:
    key = meta_path.stem
    extension = str(meta.get("extension") or "").strip().lower()
    if extension:
        if not extension.startswith("."):
            extension = f".{extension}"
        candidate = meta_path.parent / f"{key}{extension}"
        if candidate.exists() and candidate.is_file():
            return candidate
    for candidate in meta_path.parent.glob(f"{key}.*"):
        if candidate.suffix.lower() == ".json":
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _write_image_cache_meta(meta_path: Path, payload: dict[str, object]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload), encoding="utf-8")


def _lookup_image_cache_path(image_url: str, now: datetime) -> tuple[Path | None, bool]:
    if not _image_cache_enabled():
        return None, False
    cleaned_url = str(image_url or "").strip()
    if not cleaned_url:
        return None, False

    meta_path = _image_cache_meta_path(cleaned_url)
    if not meta_path.exists() or not meta_path.is_file():
        return None, False

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None, False
    if not isinstance(meta, dict):
        return None, False

    payload_path = _image_cache_payload_path_from_meta(meta_path, meta)
    if payload_path is None:
        return None, False

    payload_size = payload_path.stat().st_size
    if payload_size <= 0:
        return None, False
    try:
        expected_size = int(meta.get("size_bytes") or 0)
    except Exception:
        expected_size = 0
    if expected_size > 0 and expected_size != payload_size:
        return None, False

    last_verified = (
        _parse_naive_utc(meta.get("last_verified_at"))
        or _parse_naive_utc(meta.get("created_at"))
        or _parse_naive_utc(meta.get("last_accessed_at"))
    )
    freshness_cutoff = now - timedelta(days=max(1, export_config.image_cache_ttl_days))
    is_fresh = bool(last_verified and last_verified >= freshness_cutoff)

    meta["last_accessed_at"] = now.isoformat()
    _write_image_cache_meta(meta_path, meta)
    return payload_path, is_fresh


def _store_image_cache_entry(
    *,
    image_url: str,
    payload: bytes,
    content_type: str,
    now: datetime,
) -> Path | None:
    if not _image_cache_enabled():
        return None
    cleaned_url = str(image_url or "").strip()
    if not cleaned_url:
        return None
    if not payload:
        return None

    meta_path = _image_cache_meta_path(cleaned_url)
    cache_dir = meta_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)

    key = meta_path.stem
    ext = _extension_for_content_type(content_type)
    payload_path = cache_dir / f"{key}{ext}"
    payload_path.write_bytes(payload)

    # Keep exactly one payload variant per key.
    for sibling in cache_dir.glob(f"{key}.*"):
        if sibling == payload_path or sibling == meta_path:
            continue
        if sibling.is_file():
            sibling.unlink(missing_ok=True)

    meta = {
        "image_url": cleaned_url,
        "content_type": content_type,
        "extension": ext,
        "size_bytes": len(payload),
        "created_at": now.isoformat(),
        "last_verified_at": now.isoformat(),
        "last_accessed_at": now.isoformat(),
    }
    _write_image_cache_meta(meta_path, meta)
    return payload_path


def _materialize_item_image_from_cache(job_id: int, item_id: int, cache_path: Path) -> str:
    ext = cache_path.suffix.lower()
    if not ext or len(ext) > 8:
        ext = ".jpg"
    relative_path = f"images/item_{item_id}{ext}"
    destination = _job_dir(job_id) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cache_path, destination)
    return relative_path


def _materialize_item_image_from_payload(
    job_id: int,
    item_id: int,
    payload: bytes,
    content_type: str,
) -> str:
    ext = _extension_for_content_type(content_type)
    relative_path = f"images/item_{item_id}{ext}"
    destination = _job_dir(job_id) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return relative_path


def prune_image_cache(*, now: datetime | None = None, max_files: int | None = None) -> int:
    if not _image_cache_enabled():
        return 0

    current = now or utc_now_naive()
    retention_days = max(1, export_config.image_cache_retention_days)
    cutoff = current - timedelta(days=retention_days)
    limit = max(1, max_files if max_files is not None else export_config.image_cache_max_prune_files)
    removed = 0

    root = _image_cache_root()
    for meta_path in root.rglob("*.json"):
        if removed >= limit:
            break
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}

        reference_time = (
            _parse_naive_utc(meta.get("last_accessed_at"))
            or _parse_naive_utc(meta.get("last_verified_at"))
            or _parse_naive_utc(meta.get("created_at"))
        )
        if reference_time is None:
            reference_time = datetime.fromtimestamp(meta_path.stat().st_mtime, tz=UTC).replace(tzinfo=None)
        if reference_time > cutoff:
            continue

        payload_path = _image_cache_payload_path_from_meta(meta_path, meta)
        if payload_path and payload_path.exists():
            payload_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        removed += 1

    # Remove orphaned payload files and empty directories.
    for payload_path in root.rglob("*"):
        if removed >= limit:
            break
        if not payload_path.is_file():
            continue
        if payload_path.suffix.lower() == ".json":
            continue
        key = payload_path.stem
        meta_path = payload_path.parent / f"{key}.json"
        if meta_path.exists():
            continue
        modified = datetime.fromtimestamp(payload_path.stat().st_mtime, tz=UTC).replace(tzinfo=None)
        if modified <= cutoff:
            payload_path.unlink(missing_ok=True)
            removed += 1

    # Best-effort empty-dir cleanup.
    for folder in sorted(root.rglob("*"), reverse=True):
        if folder.is_dir():
            try:
                folder.rmdir()
            except OSError:
                pass

    return removed


def _job_dir(job_id: int) -> Path:
    return _storage_root() / f"job_{job_id}"


def _relative_to_storage(path: Path) -> str:
    return str(path.relative_to(_storage_root()))


def _extension_for_content_type(content_type: str) -> str:
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"
    return ".jpg"


def _quota_state_path() -> Path:
    return _storage_root() / "quota_state.json"


def _load_quota_state() -> dict[str, int | str]:
    path = _quota_state_path()
    if not path.exists():
        return {
            "day_key": "",
            "day_requests": 0,
            "day_bytes": 0,
            "hour_key": "",
            "hour_bytes": 0,
        }

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            "day_key": str(raw.get("day_key") or ""),
            "day_requests": int(raw.get("day_requests") or 0),
            "day_bytes": int(raw.get("day_bytes") or 0),
            "hour_key": str(raw.get("hour_key") or ""),
            "hour_bytes": int(raw.get("hour_bytes") or 0),
        }
    except Exception:
        return {
            "day_key": "",
            "day_requests": 0,
            "day_bytes": 0,
            "hour_key": "",
            "hour_bytes": 0,
        }


def _save_quota_state(payload: dict[str, int | str]) -> None:
    path = _quota_state_path()
    path.write_text(json.dumps(payload), encoding="utf-8")


def _reset_quota_windows(payload: dict[str, int | str], now: datetime) -> None:
    day_key = now.strftime("%Y-%m-%d")
    hour_key = now.strftime("%Y-%m-%d %H")

    if payload.get("day_key") != day_key:
        payload["day_key"] = day_key
        payload["day_requests"] = 0
        payload["day_bytes"] = 0

    if payload.get("hour_key") != hour_key:
        payload["hour_key"] = hour_key
        payload["hour_bytes"] = 0


def _upsert_artifact(
    db: Session,
    job_id: int,
    kind: str,
    relative_path: str,
    part_number: int | None,
) -> None:
    existing = (
        db.query(models.ExportArtifact)
        .filter(
            models.ExportArtifact.job_id == job_id,
            models.ExportArtifact.kind == kind,
            models.ExportArtifact.part_number.is_(part_number) if part_number is None else models.ExportArtifact.part_number == part_number,
        )
        .first()
    )
    abs_path = _storage_root() / relative_path
    size_bytes = abs_path.stat().st_size if abs_path.exists() else 0

    if existing:
        existing.relative_path = relative_path
        existing.size_bytes = size_bytes
        return

    db.add(
        models.ExportArtifact(
            job_id=job_id,
            kind=kind,
            part_number=part_number,
            relative_path=relative_path,
            size_bytes=size_bytes,
        )
    )


def _build_readme_text(job: models.ExportJob) -> str:
    sort_source = _resolved_sort_source()
    sort_line = (
        "- Sorting mode: genus from iNaturalist current taxon (`taxon`).\n"
        if sort_source == "taxon"
        else "- Sorting mode: genus from observer-side taxon (`observation_taxon`).\n"
    )
    mode_line = (
        f"- Export mode: include all photos (max {max(1, min(export_config.max_photos_per_observation, 8))} per observation).\n"
        if export_config.include_all_photos
        else "- Export mode: one primary photo per observation.\n"
    )
    return (
        "Mushroom Observation PDF Export\n"
        "\n"
        "How to open this package:\n"
        "1. Find this ZIP file in your Downloads folder.\n"
        "2. Right-click the ZIP file.\n"
        "3. Click 'Extract All' (or 'Unzip').\n"
        "4. Open the new extracted folder.\n"
        "5. Open *_observations_index.pdf for the linked observation list.\n"
        "6. Open *_all_observations.pdf if present, or PART files in numeric order.\n"
        "\n"
        "Why there are multiple files:\n"
        "- Large exports are split into smaller PDFs to keep the server stable.\n"
        + mode_line +
        sort_line +
        "- PDFs are readable offline. External iNaturalist links require internet access.\n"
        "\n"
        "License and attribution notice:\n"
        "- Images are included only when their licenses are allowed by this project policy.\n"
        "- Each page contains source and attribution details from iNaturalist metadata.\n"
        f"\nExport job ID: {job.id}\n"
    )


def _filename_prefix_for_list(obs_list: models.ObservationList | None, list_id: int) -> str:
    tokens: list[str] = []
    if obs_list:
        if obs_list.county_name:
            tokens.append(obs_list.county_name)
        if obs_list.state_code:
            tokens.append(obs_list.state_code)
        if obs_list.product_type == "project":
            title_text = (obs_list.title or "").strip()
            for marker in ("— iNaturalist Project", "- iNaturalist Project"):
                if marker in title_text:
                    title_text = title_text.split(marker, 1)[0].strip(" -")
                    break
            if title_text:
                tokens.append(title_text)
            elif obs_list.inat_project_id:
                tokens.append(obs_list.inat_project_id)
        if not tokens and obs_list.title:
            tokens.append(obs_list.title)

    if not tokens:
        return f"list-{list_id}"

    raw = "-".join(tokens)
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower()
    if not cleaned:
        return f"list-{list_id}"
    return cleaned
