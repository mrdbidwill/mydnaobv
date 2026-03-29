from __future__ import annotations

import argparse

from app.core.config import settings
from app.db import SessionLocal
from app.exports.service import (
    cleanup_expired_exports,
    enqueue_due_public_refresh_jobs,
    prune_image_cache,
    process_next_job,
    run_scheduled_maintenance,
)


def run_once() -> int:
    db = SessionLocal()
    try:
        run_scheduled_maintenance(db)
        enqueue_due_public_refresh_jobs(
            db,
            limit=max(1, settings.public_auto_refresh_enqueue_per_run),
        )
        job = process_next_job(db)
        if not job:
            return 0
        return 0
    finally:
        db.close()


def run_cleanup() -> int:
    db = SessionLocal()
    try:
        cleanup_expired_exports(db)
        prune_image_cache()
        return 0
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="myDNAobv PDF export worker")
    parser.add_argument("--once", action="store_true", help="Process one eligible job once")
    parser.add_argument("--cleanup", action="store_true", help="Delete expired export artifacts")
    args = parser.parse_args()

    if args.cleanup:
        return run_cleanup()
    if args.once:
        return run_once()

    # Default behavior mirrors --once so cron setup can stay simple.
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
