from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any

try:
    import boto3
    from botocore.config import Config
except Exception:  # pragma: no cover - optional dependency at runtime
    boto3 = None
    Config = None

from app import models
from app.exports.config import export_config

BACKEND_FILESYSTEM = "filesystem"
BACKEND_S3 = "s3"
CACHE_CONTROL_LATEST = "no-cache, max-age=0, must-revalidate"
CACHE_CONTROL_IMMUTABLE = "public, max-age=31536000, immutable"


def publish_enabled() -> bool:
    if not export_config.publish_enabled:
        return False

    base_url = (export_config.publish_base_url or "").strip()
    if not base_url:
        return False

    backend = _publish_backend()
    if backend == BACKEND_S3:
        return _s3_publish_config_error() is None
    return _publish_root() is not None


def published_filename(artifact: models.ExportArtifact) -> str:
    rel_name = Path(artifact.relative_path).name
    if rel_name:
        return rel_name
    if artifact.kind == "part_pdf" and artifact.part_number:
        return f"part_{artifact.part_number:03d}.pdf"
    return f"{artifact.kind}.bin"


def published_job_url(list_id: int, job_id: int, artifact: models.ExportArtifact) -> str | None:
    if not publish_enabled():
        return None
    return _url_join(
        str(export_config.publish_base_url or ""),
        f"list_{list_id}/job_{job_id}/{published_filename(artifact)}",
    )


def published_latest_url(list_id: int, artifact: models.ExportArtifact) -> str | None:
    if not publish_enabled():
        return None
    latest = _url_join(
        str(export_config.publish_base_url or ""),
        f"list_{list_id}/latest/{published_filename(artifact)}",
    )
    # Add a stable per-artifact version token so "latest" links don't serve stale CDN cache.
    return f"{latest}?v={artifact.id}"


def latest_artifact_exists(list_id: int, artifact: models.ExportArtifact) -> bool:
    if not publish_enabled():
        return False

    if _publish_backend() == BACKEND_S3:
        job_id = int(getattr(artifact, "job_id", 0) or 0)
        if job_id <= 0:
            return False
        state = _load_publish_state(list_id)
        if int(state.get("latest_job_id") or 0) < job_id:
            return False
        latest_files = state.get("latest_filenames")
        if isinstance(latest_files, list):
            expected = published_filename(artifact)
            names = {str(item).strip() for item in latest_files if str(item).strip()}
            return expected in names
        if artifact.kind == "zip_chunk":
            return False
        return True

    root = _publish_root()
    if not root:
        return False
    return (root / f"list_{list_id}" / "latest" / published_filename(artifact)).exists()


def publish_job_artifacts(
    job: models.ExportJob,
    artifacts: list[models.ExportArtifact],
    storage_root: Path,
) -> str | None:
    if not export_config.publish_enabled:
        return None

    base_url = (export_config.publish_base_url or "").strip()
    if not base_url:
        return "publish enabled but EXPORT_PUBLISH_BASE_URL is missing."

    warning: str | None
    if _publish_backend() == BACKEND_S3:
        warning = _publish_job_artifacts_s3(job, artifacts, storage_root)
    else:
        root = _publish_root()
        if not root:
            return "publish enabled but EXPORT_PUBLISH_DIR is missing."
        warning = _publish_job_artifacts_filesystem(job, artifacts, storage_root, root, base_url)

    if warning is None:
        filenames = [published_filename(artifact) for artifact in artifacts]
        _mark_latest_job_published(job.list_id, job.id, filenames)
    return warning


def is_latest_job_published(list_id: int, job_id: int) -> bool:
    if not publish_enabled():
        return False
    state = _load_publish_state(list_id)
    return int(state.get("latest_job_id") or 0) >= int(job_id)


def cleanup_published_job(list_id: int, job_id: int) -> None:
    if _publish_backend() == BACKEND_S3:
        _delete_s3_prefix(_object_key(f"list_{list_id}/job_{job_id}/"))
        return

    root = _publish_root()
    if not root:
        return
    job_dir = root / f"list_{list_id}" / f"job_{job_id}"
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


def _publish_job_artifacts_filesystem(
    job: models.ExportJob,
    artifacts: list[models.ExportArtifact],
    storage_root: Path,
    root: Path,
    base_url: str,
) -> str | None:
    list_root = root / f"list_{job.list_id}"
    job_dir = list_root / f"job_{job.id}"
    latest_dir = list_root / "latest"
    job_tmp = list_root / f"job_{job.id}.tmp"
    latest_tmp = list_root / "latest.tmp"

    missing_files: list[str] = []
    published_rows: list[dict[str, str | int | None]] = []

    try:
        if job_tmp.exists():
            shutil.rmtree(job_tmp, ignore_errors=True)
        if latest_tmp.exists():
            shutil.rmtree(latest_tmp, ignore_errors=True)

        job_tmp.mkdir(parents=True, exist_ok=True)
        latest_tmp.mkdir(parents=True, exist_ok=True)

        for artifact in artifacts:
            src = storage_root / artifact.relative_path
            filename = published_filename(artifact)
            if not src.exists() or not src.is_file():
                missing_files.append(filename)
                continue

            job_dest = job_tmp / filename
            latest_dest = latest_tmp / filename
            shutil.copy2(src, job_dest)
            shutil.copy2(src, latest_dest)

            published_rows.append(
                {
                    "artifact_id": artifact.id,
                    "kind": artifact.kind,
                    "part_number": artifact.part_number,
                    "filename": filename,
                    "job_url": _url_join(base_url, f"list_{job.list_id}/job_{job.id}/{filename}"),
                    "latest_url": published_latest_url(job.list_id, artifact),
                }
            )

        manifest = {
            "list_id": job.list_id,
            "job_id": job.id,
            "status": job.status,
            "published_at_utc": datetime.now(UTC).isoformat(),
            "files": published_rows,
        }
        (job_tmp / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (latest_tmp / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        _replace_dir(job_tmp, job_dir)
        _replace_dir(latest_tmp, latest_dir)
    except Exception as exc:
        shutil.rmtree(job_tmp, ignore_errors=True)
        shutil.rmtree(latest_tmp, ignore_errors=True)
        return f"publish failed: {exc}"

    if not published_rows:
        return "publish produced no files."
    if missing_files:
        missing_text = ", ".join(sorted(set(missing_files)))
        return f"publish completed with missing files: {missing_text}"
    return None


def _publish_job_artifacts_s3(
    job: models.ExportJob,
    artifacts: list[models.ExportArtifact],
    storage_root: Path,
) -> str | None:
    config_error = _s3_publish_config_error()
    if config_error:
        return config_error

    try:
        client = _s3_client()
        bucket = str(export_config.publish_bucket or "")

        missing_files: list[str] = []
        published_rows: list[dict[str, str | int | None]] = []

        for artifact in artifacts:
            src = storage_root / artifact.relative_path
            filename = published_filename(artifact)
            if not src.exists() or not src.is_file():
                missing_files.append(filename)
                continue

            job_key = _object_key(f"list_{job.list_id}/job_{job.id}/{filename}")
            latest_key = _object_key(f"list_{job.list_id}/latest/{filename}")
            client.upload_file(
                str(src),
                bucket,
                job_key,
                ExtraArgs={"CacheControl": CACHE_CONTROL_IMMUTABLE},
            )
            client.upload_file(
                str(src),
                bucket,
                latest_key,
                ExtraArgs={"CacheControl": CACHE_CONTROL_LATEST},
            )

            published_rows.append(
                {
                    "artifact_id": artifact.id,
                    "kind": artifact.kind,
                    "part_number": artifact.part_number,
                    "filename": filename,
                    "job_url": _url_join(
                        str(export_config.publish_base_url or ""),
                        f"list_{job.list_id}/job_{job.id}/{filename}",
                    ),
                    "latest_url": published_latest_url(job.list_id, artifact),
                }
            )

        manifest = {
            "list_id": job.list_id,
            "job_id": job.id,
            "status": job.status,
            "published_at_utc": datetime.now(UTC).isoformat(),
            "files": published_rows,
        }
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        client.put_object(
            Bucket=bucket,
            Key=_object_key(f"list_{job.list_id}/job_{job.id}/manifest.json"),
            Body=payload,
            ContentType="application/json",
            CacheControl=CACHE_CONTROL_IMMUTABLE,
        )
        client.put_object(
            Bucket=bucket,
            Key=_object_key(f"list_{job.list_id}/latest/manifest.json"),
            Body=payload,
            ContentType="application/json",
            CacheControl=CACHE_CONTROL_LATEST,
        )
    except Exception as exc:
        return f"publish failed: {exc}"

    if not published_rows:
        return "publish produced no files."
    if missing_files:
        missing_text = ", ".join(sorted(set(missing_files)))
        return f"publish completed with missing files: {missing_text}"
    return None


def _publish_root() -> Path | None:
    configured = (export_config.publish_dir or "").strip()
    if not configured:
        return None
    root = Path(configured)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _publish_state_root() -> Path:
    root = Path(export_config.storage_dir) / "publish_state"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _publish_state_path(list_id: int) -> Path:
    return _publish_state_root() / f"list_{int(list_id)}.json"


def _load_publish_state(list_id: int) -> dict[str, object]:
    path = _publish_state_path(list_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _save_publish_state(list_id: int, payload: dict[str, object]) -> None:
    path = _publish_state_path(list_id)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _mark_latest_job_published(list_id: int, job_id: int, filenames: list[str] | None = None) -> None:
    now_iso = datetime.now(UTC).isoformat()
    state = _load_publish_state(list_id)
    latest_job_id = int(state.get("latest_job_id") or 0)
    if int(job_id) < latest_job_id:
        return
    clean_filenames = sorted({str(name).strip() for name in (filenames or []) if str(name).strip()})
    state["latest_job_id"] = int(job_id)
    state["published_at_utc"] = now_iso
    state["latest_filenames"] = clean_filenames
    _save_publish_state(list_id, state)


def _publish_backend() -> str:
    raw = (export_config.publish_backend or "").strip().lower()
    if raw in (BACKEND_S3, BACKEND_FILESYSTEM):
        return raw
    return BACKEND_FILESYSTEM


def _s3_publish_config_error() -> str | None:
    if not (export_config.publish_bucket or "").strip():
        return "publish backend s3 requires EXPORT_PUBLISH_BUCKET."
    if not (export_config.publish_s3_endpoint or "").strip():
        return "publish backend s3 requires EXPORT_PUBLISH_S3_ENDPOINT."
    if not (export_config.publish_s3_access_key_id or "").strip():
        return "publish backend s3 requires EXPORT_PUBLISH_S3_ACCESS_KEY_ID."
    if not (export_config.publish_s3_secret_access_key or "").strip():
        return "publish backend s3 requires EXPORT_PUBLISH_S3_SECRET_ACCESS_KEY."
    return None


def _s3_client() -> Any:
    if boto3 is None or Config is None:
        raise RuntimeError("boto3 is required for EXPORT_PUBLISH_BACKEND=s3")

    config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
    return boto3.client(
        "s3",
        endpoint_url=(export_config.publish_s3_endpoint or "").strip(),
        region_name=(export_config.publish_s3_region or "auto").strip() or "auto",
        aws_access_key_id=(export_config.publish_s3_access_key_id or "").strip(),
        aws_secret_access_key=(export_config.publish_s3_secret_access_key or "").strip(),
        config=config,
    )


def _object_key(suffix: str) -> str:
    prefix = (export_config.publish_prefix or "").strip().strip("/")
    trimmed_suffix = suffix.lstrip("/")
    if prefix:
        return f"{prefix}/{trimmed_suffix}"
    return trimmed_suffix


def _delete_s3_prefix(prefix: str) -> None:
    config_error = _s3_publish_config_error()
    if config_error:
        return

    client = _s3_client()
    bucket = str(export_config.publish_bucket or "")
    continuation_token: str | None = None

    while True:
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": prefix,
            "MaxKeys": 1000,
        }
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token

        response = client.list_objects_v2(**kwargs)
        contents = response.get("Contents") or []
        if contents:
            batch = [{"Key": item["Key"]} for item in contents if item.get("Key")]
            if batch:
                client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})

        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")


def _replace_dir(source_tmp: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    source_tmp.replace(target)


def _url_join(base: str, suffix: str) -> str:
    return f"{base.rstrip('/')}/{suffix.lstrip('/')}"
