#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

from sqlalchemy import func

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import models
from app.db import SessionLocal
from app.services.us_counties import normalize_state_code


def _issue_type(item: models.ExportItem) -> str:
    if item.status == "failed":
        if (item.skip_reason or "").strip().lower() == "download_failed":
            return "download_failed"
        return "item_failed"

    reason = (item.skip_reason or "").strip().lower()
    if reason.startswith("license:"):
        return "license_restricted"
    if reason == "no_image_url":
        return "missing_image"
    if reason:
        return reason
    return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export problematic county observations from county export jobs to CSV for follow-up."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("latest", "latest_failed"),
        default="latest",
        help=(
            "Job scope: latest = latest job per county list (default); "
            "latest_failed = latest failed job per county list."
        ),
    )
    parser.add_argument(
        "--output",
        default="reports/problem_observations_latest.csv",
        help="CSV output path (default: reports/problem_observations_latest.csv).",
    )
    parser.add_argument(
        "--state",
        default="",
        help="Optional two-letter state code filter (example: AL).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="Optional lookback in days for export jobs (0 = no lookback filter).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state_code = normalize_state_code(args.state or "")
    lookback_days = max(0, int(args.days or 0))
    cutoff: datetime | None = None
    if lookback_days > 0:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=lookback_days)

    db = SessionLocal()
    try:
        job_scope_query = db.query(
            models.ExportJob.list_id.label("list_id"),
            func.max(models.ExportJob.id).label("job_id"),
        )
        if args.mode == "latest_failed":
            job_scope_query = job_scope_query.filter(models.ExportJob.status == "failed")
        latest_job_subq = job_scope_query.group_by(models.ExportJob.list_id).subquery()

        rows = (
            db.query(
                models.ObservationList,
                models.ExportJob,
                models.ExportItem,
                models.Observation,
            )
            .join(latest_job_subq, latest_job_subq.c.list_id == models.ObservationList.id)
            .join(models.ExportJob, models.ExportJob.id == latest_job_subq.c.job_id)
            .join(models.ExportItem, models.ExportItem.job_id == models.ExportJob.id)
            .outerjoin(models.Observation, models.Observation.id == models.ExportItem.observation_id)
            .filter(models.ObservationList.product_type == "county")
            .filter(
                (models.ExportItem.status == "failed")
                | (
                    (models.ExportItem.status == "skipped")
                    & (models.ExportItem.skip_reason.isnot(None))
                )
            )
            .order_by(
                models.ObservationList.state_code.asc().nullslast(),
                models.ObservationList.county_name.asc().nullslast(),
                models.ExportItem.id.asc(),
            )
            .all()
        )

        if state_code:
            rows = [row for row in rows if row[0].state_code == state_code]
        if cutoff is not None:
            rows = [row for row in rows if row[1].created_at and row[1].created_at >= cutoff]

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        headers = [
            "state_code",
            "county_name",
            "list_id",
            "list_title",
            "inat_project_id",
            "job_id",
            "job_status",
            "job_created_at_utc",
            "job_finished_at_utc",
            "item_id",
            "item_status",
            "issue_type",
            "skip_reason",
            "error_message",
            "attempts",
            "inat_observation_id",
            "inat_url",
            "observer",
            "observed_at",
            "image_license_code",
            "image_url",
        ]

        issue_counts: Counter[str] = Counter()
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()

            for obs_list, job, item, observation in rows:
                issue = _issue_type(item)
                issue_counts[issue] += 1

                writer.writerow(
                    {
                        "state_code": obs_list.state_code or "",
                        "county_name": obs_list.county_name or "",
                        "list_id": obs_list.id,
                        "list_title": obs_list.title,
                        "inat_project_id": obs_list.inat_project_id or "",
                        "job_id": job.id,
                        "job_status": job.status,
                        "job_created_at_utc": (job.created_at.isoformat() if job.created_at else ""),
                        "job_finished_at_utc": (job.finished_at.isoformat() if job.finished_at else ""),
                        "item_id": item.id,
                        "item_status": item.status,
                        "issue_type": issue,
                        "skip_reason": item.skip_reason or "",
                        "error_message": item.error_message or "",
                        "attempts": item.attempts,
                        "inat_observation_id": item.inat_observation_id,
                        "inat_url": item.inat_url,
                        "observer": (observation.user_name if observation else ""),
                        "observed_at": (
                            observation.observed_at.isoformat()
                            if observation and observation.observed_at
                            else ""
                        ),
                        "image_license_code": item.image_license_code or "",
                        "image_url": item.image_url or "",
                    }
                )

        print(f"Wrote {len(rows)} rows to {output_path} (mode={args.mode})")
        if issue_counts:
            print("Issue counts:")
            for issue_name, count in sorted(issue_counts.items(), key=lambda x: (-x[1], x[0])):
                print(f"- {issue_name}: {count}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
