from __future__ import annotations

import argparse
import fcntl
from pathlib import Path
from typing import TextIO

from app.core.config import settings
from app.db import SessionLocal
from app.exports.service import (
    cleanup_expired_exports,
    enqueue_due_public_refresh_jobs,
    process_pending_publish_jobs,
    prune_image_cache,
    process_next_job,
    run_scheduled_maintenance,
)


def _housekeeping_lock_path() -> Path:
    path = Path(settings.export_storage_dir) / "worker_housekeeping.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _try_acquire_housekeeping_lock() -> TextIO | None:
    handle = _housekeeping_lock_path().open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except (BlockingIOError, OSError):
        handle.close()
        return None


def _release_housekeeping_lock(handle: TextIO | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass


def run_once() -> int:
    housekeeping_lock = _try_acquire_housekeeping_lock()
    db = SessionLocal()
    try:
        if housekeeping_lock is not None:
            run_scheduled_maintenance(db)
            enqueue_due_public_refresh_jobs(
                db,
                limit=max(1, settings.public_auto_refresh_enqueue_per_run),
            )
        process_next_job(db)
        process_pending_publish_jobs(
            db,
            limit=max(1, settings.export_publish_jobs_per_run),
        )
        return 0
    finally:
        _release_housekeeping_lock(housekeeping_lock)
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
