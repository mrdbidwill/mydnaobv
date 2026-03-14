#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from app.core.config import settings

try:
    import boto3
    from botocore.config import Config
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"boto3 is required: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sync existing published export files from local directory to configured "
            "S3-compatible object storage (Cloudflare R2 compatible)."
        )
    )
    parser.add_argument(
        "--source-dir",
        default=settings.export_publish_dir or "",
        help="Local publish directory to scan (defaults to EXPORT_PUBLISH_DIR).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload files. Without this flag, command is a dry-run.",
    )
    return parser.parse_args()


def build_s3_client():
    endpoint = (settings.export_publish_s3_endpoint or "").strip()
    access_key = (settings.export_publish_s3_access_key_id or "").strip()
    secret_key = (settings.export_publish_s3_secret_access_key or "").strip()
    region = (settings.export_publish_s3_region or "auto").strip() or "auto"

    missing = []
    if not endpoint:
        missing.append("EXPORT_PUBLISH_S3_ENDPOINT")
    if not access_key:
        missing.append("EXPORT_PUBLISH_S3_ACCESS_KEY_ID")
    if not secret_key:
        missing.append("EXPORT_PUBLISH_S3_SECRET_ACCESS_KEY")
    if missing:
        raise ValueError(f"Missing required config: {', '.join(missing)}")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def object_key_for(relative_path: Path) -> str:
    prefix = (settings.export_publish_prefix or "").strip().strip("/")
    suffix = str(relative_path).replace("\\", "/").lstrip("/")
    if prefix:
        return f"{prefix}/{suffix}"
    return suffix


def iter_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def main() -> int:
    args = parse_args()

    bucket = (settings.export_publish_bucket or "").strip()
    if not bucket:
        raise SystemExit("EXPORT_PUBLISH_BUCKET is required.")

    source_dir = Path((args.source_dir or "").strip())
    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"Source directory not found: {source_dir}")

    files = iter_files(source_dir)
    if not files:
        print(f"No files found in {source_dir}")
        return 0

    print(f"Source directory: {source_dir}")
    print(f"Bucket: {bucket}")
    print(f"Prefix: {(settings.export_publish_prefix or '').strip() or '(none)'}")
    print(f"Files found: {len(files)}")

    if not args.apply:
        print("Dry-run mode. Re-run with --apply to upload.")
        preview = min(10, len(files))
        for path in files[:preview]:
            rel = path.relative_to(source_dir)
            print(f"  - {rel} -> {object_key_for(rel)}")
        if len(files) > preview:
            print(f"  ... and {len(files) - preview} more files")
        return 0

    client = build_s3_client()

    uploaded = 0
    for path in files:
        rel = path.relative_to(source_dir)
        key = object_key_for(rel)
        client.upload_file(str(path), bucket, key)
        uploaded += 1

    print(f"Uploaded files: {uploaded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
