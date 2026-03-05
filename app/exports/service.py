from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import shutil
import time
import zipfile

import httpx
from pypdf import PdfReader, PdfWriter
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app import models
from app.exports.config import export_config
from app.exports.publish import cleanup_published_job, publish_job_artifacts
from app.exports.pdf_writer import render_part_pdf
from app.exports.policy import evaluate_license, normalize_license_code

ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_quota")
FINISHED_JOB_STATUSES = ("ready", "partial_ready", "failed", "canceled")


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def enqueue_export_job(db: Session, list_id: int, requested_by: str | None = None) -> models.ExportJob:
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
        part_size=max(10, export_config.part_size),
        next_run_at=utc_now_naive(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, True, f"Queued export job #{job.id}."


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

    if job.status in ("queued", "waiting_quota"):
        job.status = "running"
    if not job.started_at:
        job.started_at = now

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
            job.updated_at = utc_now_naive()
            db.commit()

        return job
    except Exception as exc:
        job.status = "failed"
        job.message = f"worker_error: {exc}"
        job.finished_at = utc_now_naive()
        job.updated_at = utc_now_naive()
        db.commit()
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


def _pick_next_job(db: Session, now: datetime) -> models.ExportJob | None:
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
            models.ExportJob.status.in_(ACTIVE_JOB_STATUSES),
            (models.ExportJob.next_run_at.is_(None) | (models.ExportJob.next_run_at <= now)),
        )
        .order_by(bucket_rank.asc(), models.ExportJob.created_at.asc())
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

    observations = (
        db.query(models.Observation)
        .filter(models.Observation.list_id == job.list_id)
        .order_by(models.Observation.observed_at.desc().nullslast(), models.Observation.id.asc())
        .all()
    )
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
                    item_title=obs.scientific_name or obs.species_guess or obs.taxon_name,
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
            title = obs.scientific_name or obs.species_guess or obs.taxon_name
            if export_config.include_all_photos and total_candidates > 1:
                title = f"{title or f'Observation {obs.inat_observation_id}'} (photo {idx}/{total_candidates})"

            db.add(
                models.ExportItem(
                    job_id=job.id,
                    observation_id=obs.id,
                    sequence=sequence,
                    inat_observation_id=obs.inat_observation_id,
                    item_title=title,
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
        job.status = "failed"
        job.phase = "done"
        job.message = "No exportable observations. All records missing images or excluded by license policy."
        job.finished_at = utc_now_naive()
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

            if quota["day_requests"] >= export_config.max_api_requests_per_day:
                job.status = "waiting_quota"
                job.message = "Paused: reached daily API request budget."
                job.next_run_at = now + timedelta(hours=1)
                _save_quota_state(quota)
                return False

            if quota["day_bytes"] >= export_config.max_media_mb_per_day * 1024 * 1024:
                job.status = "waiting_quota"
                job.message = "Paused: reached daily media download budget."
                job.next_run_at = now + timedelta(hours=1)
                _save_quota_state(quota)
                return False

            if quota["hour_bytes"] >= export_config.max_media_mb_per_hour * 1024 * 1024:
                job.status = "waiting_quota"
                job.message = "Paused: reached hourly media download budget."
                job.next_run_at = now + timedelta(hours=1)
                _save_quota_state(quota)
                return False

            if run_bytes >= run_byte_budget:
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

                ext = _extension_for_content_type(content_type)
                relative_path = f"images/item_{item.id}{ext}"
                destination = _job_dir(job.id) / relative_path
                destination.write_bytes(payload)

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


def _phase_finalize(db: Session, job: models.ExportJob) -> bool:
    parts = (
        db.query(models.ExportArtifact)
        .filter(models.ExportArtifact.job_id == job.id, models.ExportArtifact.kind == "part_pdf")
        .order_by(models.ExportArtifact.part_number.asc())
        .all()
    )
    if not parts:
        job.status = "failed"
        job.phase = "done"
        job.message = "No PDF parts were generated."
        job.finished_at = utc_now_naive()
        return True

    docs_dir = _job_dir(job.id) / "final"
    docs_dir.mkdir(parents=True, exist_ok=True)

    readme_path = docs_dir / "README_FIRST.txt"
    readme_path.write_text(_build_readme_text(job), encoding="utf-8")
    _upsert_artifact(db, job.id, "readme", _relative_to_storage(readme_path), None)

    merged_created = False
    if len(parts) <= max(1, export_config.zip_only_part_threshold):
        merged_path = docs_dir / "all_observations.pdf"
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

    zip_path = docs_dir / "observation_export_parts.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(readme_path, arcname="README_FIRST.txt")
        for part in parts:
            part_path = _storage_root() / part.relative_path
            arcname = f"parts/{Path(part.relative_path).name}"
            zf.write(part_path, arcname=arcname)
        if merged_created:
            merged_path = docs_dir / "all_observations.pdf"
            zf.write(merged_path, arcname="all_observations.pdf")

    _upsert_artifact(db, job.id, "zip", _relative_to_storage(zip_path), None)

    db.flush()
    artifacts = (
        db.query(models.ExportArtifact)
        .filter(models.ExportArtifact.job_id == job.id)
        .order_by(models.ExportArtifact.id.asc())
        .all()
    )

    job.phase = "done"
    job.status = "ready" if merged_created else "partial_ready"
    job.finished_at = utc_now_naive()
    job.message = (
        "Export complete: merged PDF and ZIP ready."
        if merged_created
        else "Export complete: ZIP with split PDF parts ready."
    )
    publish_warning = publish_job_artifacts(job, artifacts, _storage_root())
    if publish_warning:
        job.message = f"{job.message} Publish note: {publish_warning}"
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
        "5. Open PART files in numeric order (Part 001, Part 002, and so on).\n"
        "\n"
        "Why there are multiple files:\n"
        "- Large exports are split into smaller PDFs to keep the server stable.\n"
        + mode_line +
        "\n"
        "License and attribution notice:\n"
        "- Images are included only when their licenses are allowed by this project policy.\n"
        "- Each page contains source and attribution details from iNaturalist metadata.\n"
        f"\nExport job ID: {job.id}\n"
    )
