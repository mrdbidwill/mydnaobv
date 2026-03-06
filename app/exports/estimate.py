from __future__ import annotations

from math import ceil
from statistics import median

from sqlalchemy.orm import Session

from app import models
from app.exports.config import export_config
from app.exports.policy import evaluate_license


def estimate_list_export_eta(db: Session, list_id: int) -> dict[str, object]:
    observations = (
        db.query(models.Observation)
        .filter(models.Observation.list_id == list_id)
        .order_by(models.Observation.id.asc())
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

    candidate_items = 0
    eligible_items = 0
    skipped_no_image = 0
    skipped_license = 0
    for obs in observations:
        candidates = _photo_candidates_for_observation(obs, photos_by_observation.get(obs.id, []))
        if not candidates:
            skipped_no_image += 1
            continue
        candidate_items += len(candidates)
        for candidate in candidates:
            decision = evaluate_license(candidate["license_code"])
            if decision.allowed:
                eligible_items += 1
            else:
                skipped_license += 1

    bucket = export_config.classify_bucket(eligible_items)
    avg_bytes = _historical_avg_bytes_per_item(db, list_id=list_id) or _historical_avg_bytes_per_item(db, list_id=None)
    ranges = estimate_eta_ranges_for_items(eligible_items, bucket=bucket, avg_bytes_per_item=avg_bytes)

    return {
        "observation_count": len(observations),
        "candidate_items": candidate_items,
        "eligible_items": eligible_items,
        "skipped_no_image": skipped_no_image,
        "skipped_license": skipped_license,
        "bucket": bucket,
        "include_all_photos": export_config.include_all_photos,
        "max_photos_per_observation": max(1, min(export_config.max_photos_per_observation, 8)),
        "runs_per_day": ranges["runs_per_day"],
        "items_per_run": ranges["items_per_run"],
        "items_per_day": ranges["items_per_day"],
        "eta_best": ranges["best_label"],
        "eta_likely": ranges["likely_label"],
        "eta_worst": ranges["worst_label"],
        "avg_bytes_per_item": avg_bytes,
    }


def estimate_precheck_from_observations(total_observations: int) -> dict[str, object]:
    observations = max(0, int(total_observations))
    # Rough assumption before sync/license checks:
    # one photo in primary mode, or around two photos in all-photos mode with conservative cap.
    if export_config.include_all_photos:
        max_photos = max(1, min(export_config.max_photos_per_observation, 8))
        photo_factor = min(float(max_photos), 2.0)
    else:
        photo_factor = 1.0

    candidate_items = int(round(observations * photo_factor))
    # Rough policy effect: about 70% remain after image/license filtering.
    eligible_items = int(round(candidate_items * 0.70))
    bucket = export_config.classify_bucket(eligible_items)
    ranges = estimate_eta_ranges_for_items(eligible_items, bucket=bucket, avg_bytes_per_item=None)
    return {
        "observation_count": observations,
        "candidate_items": candidate_items,
        "eligible_items": eligible_items,
        "bucket": bucket,
        "eta_best": ranges["best_label"],
        "eta_likely": ranges["likely_label"],
        "eta_worst": ranges["worst_label"],
    }


def estimate_eta_ranges_for_items(
    item_count: int,
    *,
    bucket: str | None = None,
    avg_bytes_per_item: float | None = None,
) -> dict[str, object]:
    items = max(0, int(item_count))
    chosen_bucket = bucket or export_config.classify_bucket(items)
    cadence_minutes = max(1.0, export_config.cadence_for_bucket(chosen_bucket).total_seconds() / 60.0)
    runs_per_day = max(1, int(_active_minutes_per_day(chosen_bucket) // cadence_minutes))

    base_chunk = _effective_download_chunk_size()
    items_by_budget = base_chunk
    if avg_bytes_per_item and avg_bytes_per_item > 0:
        run_budget_mb = max(1, export_config.download_byte_budget_mb)
        if export_config.include_all_photos:
            run_budget_mb = min(run_budget_mb, 40)
        items_by_budget = max(1, int((run_budget_mb * 1024 * 1024) // avg_bytes_per_item))
    items_per_run = max(1, min(base_chunk, items_by_budget))

    items_per_day = max(1, items_per_run * runs_per_day)
    best_days = _safe_days(items, items_per_day)
    likely_days = _safe_days(items, max(1, int(items_per_day * 0.65)))
    worst_days = _safe_days(items, max(1, int(items_per_day * 0.35)))

    return {
        "items_per_run": items_per_run,
        "runs_per_day": runs_per_day,
        "items_per_day": items_per_day,
        "best_days": best_days,
        "likely_days": likely_days,
        "worst_days": worst_days,
        "best_label": _duration_label(best_days),
        "likely_label": _duration_label(likely_days),
        "worst_label": _duration_label(worst_days),
    }


def _photo_candidates_for_observation(
    obs: models.Observation,
    photos: list[models.ObservationPhoto],
) -> list[dict[str, str | None]]:
    if export_config.include_all_photos:
        max_per_obs = max(1, min(export_config.max_photos_per_observation, 8))
        selected = photos[:max_per_obs]
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
    if obs.photo_url:
        return [
            {
                "url": obs.photo_url,
                "license_code": obs.photo_license_code,
                "attribution": obs.photo_attribution,
            }
        ]
    if photos and photos[0].photo_url:
        first = photos[0]
        return [
            {
                "url": first.photo_url,
                "license_code": first.photo_license_code,
                "attribution": first.photo_attribution,
            }
        ]
    return []


def _historical_avg_bytes_per_item(db: Session, list_id: int | None) -> float | None:
    query = db.query(models.ExportJob).filter(
        models.ExportJob.bytes_downloaded > 0,
        models.ExportJob.downloaded_items > 0,
    )
    if list_id is not None:
        query = query.filter(models.ExportJob.list_id == list_id)
    jobs = query.order_by(models.ExportJob.id.desc()).limit(12).all()
    samples: list[float] = []
    for job in jobs:
        if job.downloaded_items <= 0:
            continue
        samples.append(float(job.bytes_downloaded) / float(job.downloaded_items))
    if not samples:
        return None
    return float(median(samples))


def _effective_download_chunk_size() -> int:
    chunk_size = max(1, export_config.download_chunk_size)
    if export_config.include_all_photos:
        return min(chunk_size, 4)
    return chunk_size


def _active_minutes_per_day(bucket: str) -> int:
    if bucket != "L":
        return 24 * 60
    start = int(export_config.l_window_start_hour) % 24
    end = int(export_config.l_window_end_hour) % 24
    if start == end:
        return 24 * 60
    if start < end:
        return (end - start) * 60
    return ((24 - start) + end) * 60


def _safe_days(items: int, items_per_day: int) -> float:
    if items <= 0:
        return 0.0
    return float(items) / float(max(1, items_per_day)) * 1.15


def _duration_label(days: float) -> str:
    if days <= 0:
        return "ready quickly once queued"
    if days < 1:
        return f"about {max(1, ceil(days * 24.0))} hours"
    if days < 14:
        return f"about {max(1, ceil(days))} days"
    return f"about {max(2, ceil(days / 7.0))} weeks"
