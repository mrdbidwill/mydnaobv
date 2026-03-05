from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from app.exports import publish as publish_module


def test_publish_job_artifacts_writes_job_and_latest_files(tmp_path, monkeypatch):
    cfg = replace(
        publish_module.export_config,
        publish_enabled=True,
        publish_dir=str(tmp_path / "published"),
        publish_base_url="https://downloads.example.org/mydnaobv",
    )
    monkeypatch.setattr(publish_module, "export_config", cfg)

    storage_root = tmp_path / "exports"
    source_dir = storage_root / "job_11" / "final"
    source_dir.mkdir(parents=True, exist_ok=True)
    merged = source_dir / "all_observations.pdf"
    zip_file = source_dir / "observation_export_parts.zip"
    merged.write_bytes(b"pdf-bytes")
    zip_file.write_bytes(b"zip-bytes")

    job = SimpleNamespace(id=11, list_id=5, status="ready")
    artifacts = [
        SimpleNamespace(
            id=101,
            kind="merged_pdf",
            part_number=None,
            relative_path="job_11/final/all_observations.pdf",
        ),
        SimpleNamespace(
            id=102,
            kind="zip",
            part_number=None,
            relative_path="job_11/final/observation_export_parts.zip",
        ),
    ]

    warning = publish_module.publish_job_artifacts(job, artifacts, storage_root)
    assert warning is None

    assert (tmp_path / "published" / "list_5" / "job_11" / "all_observations.pdf").read_bytes() == b"pdf-bytes"
    assert (tmp_path / "published" / "list_5" / "latest" / "all_observations.pdf").read_bytes() == b"pdf-bytes"
    assert (tmp_path / "published" / "list_5" / "latest" / "manifest.json").exists()

    latest_url = publish_module.published_latest_url(5, artifacts[0])
    assert latest_url == "https://downloads.example.org/mydnaobv/list_5/latest/all_observations.pdf"


def test_publish_job_artifacts_reports_misconfiguration(tmp_path, monkeypatch):
    cfg = replace(
        publish_module.export_config,
        publish_enabled=True,
        publish_dir=str(tmp_path / "published"),
        publish_base_url=None,
    )
    monkeypatch.setattr(publish_module, "export_config", cfg)

    job = SimpleNamespace(id=1, list_id=1, status="ready")
    artifacts = [SimpleNamespace(id=1, kind="zip", part_number=None, relative_path="missing.zip")]
    warning = publish_module.publish_job_artifacts(job, artifacts, Path(tmp_path / "exports"))
    assert warning is not None
    assert "missing" in warning
