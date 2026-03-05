from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil

from app import models
from app.exports.config import export_config


def publish_enabled() -> bool:
    return bool(export_config.publish_enabled and _publish_root() and export_config.publish_base_url)


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
    return _url_join(
        str(export_config.publish_base_url or ""),
        f"list_{list_id}/latest/{published_filename(artifact)}",
    )


def latest_artifact_exists(list_id: int, artifact: models.ExportArtifact) -> bool:
    root = _publish_root()
    if not root:
        return False
    return (root / f"list_{list_id}" / "latest" / published_filename(artifact)).exists()


def publish_job_artifacts(
    job: models.ExportJob,
    artifacts: list[models.ExportArtifact],
    storage_root: Path,
) -> str | None:
    root = _publish_root()
    base_url = (export_config.publish_base_url or "").strip()
    if not export_config.publish_enabled:
        return None
    if not root or not base_url:
        return "publish enabled but EXPORT_PUBLISH_DIR or EXPORT_PUBLISH_BASE_URL is missing."

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
                    "latest_url": _url_join(base_url, f"list_{job.list_id}/latest/{filename}"),
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


def cleanup_published_job(list_id: int, job_id: int) -> None:
    root = _publish_root()
    if not root:
        return
    job_dir = root / f"list_{list_id}" / f"job_{job_id}"
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


def _publish_root() -> Path | None:
    configured = (export_config.publish_dir or "").strip()
    if not configured:
        return None
    root = Path(configured)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _replace_dir(source_tmp: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    source_tmp.replace(target)


def _url_join(base: str, suffix: str) -> str:
    return f"{base.rstrip('/')}/{suffix.lstrip('/')}"
