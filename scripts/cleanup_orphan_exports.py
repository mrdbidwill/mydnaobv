#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from app import models
from app.core.config import settings
from app.db import SessionLocal


def _parse_suffix_id(name: str, prefix: str) -> int | None:
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix):]
    if not suffix.isdigit():
        return None
    return int(suffix)


def _remove_path(path: Path, apply: bool) -> bool:
    if not path.exists():
        return False
    if not apply:
        return True
    shutil.rmtree(path, ignore_errors=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find and optionally delete orphan export directories that no longer "
            "have matching DB records."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete orphan directories. Without this flag, script only reports findings.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        live_job_ids = {row[0] for row in db.query(models.ExportJob.id).all()}
        live_list_ids = {row[0] for row in db.query(models.ObservationList.id).all()}
    finally:
        db.close()

    storage_root = Path(settings.export_storage_dir)
    publish_root = Path(settings.export_publish_dir) if (settings.export_publish_dir or "").strip() else None

    orphan_storage_dirs: list[Path] = []
    if storage_root.exists():
        for child in storage_root.iterdir():
            if not child.is_dir():
                continue
            job_id = _parse_suffix_id(child.name, "job_")
            if job_id is None:
                continue
            if job_id not in live_job_ids:
                orphan_storage_dirs.append(child)

    orphan_publish_list_dirs: list[Path] = []
    orphan_publish_job_dirs: list[Path] = []
    if publish_root and publish_root.exists():
        for list_dir in publish_root.iterdir():
            if not list_dir.is_dir():
                continue
            list_id = _parse_suffix_id(list_dir.name, "list_")
            if list_id is None:
                continue
            if list_id not in live_list_ids:
                orphan_publish_list_dirs.append(list_dir)
                continue
            for child in list_dir.iterdir():
                if not child.is_dir():
                    continue
                job_id = _parse_suffix_id(child.name, "job_")
                if job_id is None:
                    continue
                if job_id not in live_job_ids:
                    orphan_publish_job_dirs.append(child)

    removed_storage = sum(1 for path in orphan_storage_dirs if _remove_path(path, args.apply))
    removed_publish_lists = sum(1 for path in orphan_publish_list_dirs if _remove_path(path, args.apply))
    removed_publish_jobs = sum(1 for path in orphan_publish_job_dirs if _remove_path(path, args.apply))

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] storage_root={storage_root}")
    print(f"[{mode}] orphan storage job dirs: {removed_storage}")
    if publish_root:
        print(f"[{mode}] publish_root={publish_root}")
        print(f"[{mode}] orphan publish list dirs: {removed_publish_lists}")
        print(f"[{mode}] orphan publish job dirs: {removed_publish_jobs}")
    else:
        print(f"[{mode}] publish_root is not configured; skipping published-dir scan.")

    if not args.apply:
        print("Re-run with --apply to delete reported orphan directories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
