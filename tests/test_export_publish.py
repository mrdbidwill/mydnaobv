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
    assert latest_url == "https://downloads.example.org/mydnaobv/list_5/latest/all_observations.pdf?v=101"


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


def test_publish_job_artifacts_s3_uploads_expected_keys(tmp_path, monkeypatch):
    class FakeS3:
        def __init__(self):
            self.upload_calls: list[tuple[str, str, str, dict | None]] = []
            self.put_calls: list[tuple[str, str, bytes, str | None]] = []

        def upload_file(self, filename, bucket, key, ExtraArgs=None):
            self.upload_calls.append((filename, bucket, key, ExtraArgs))

        def put_object(self, Bucket, Key, Body, ContentType, CacheControl=None):
            assert ContentType == "application/json"
            self.put_calls.append((Bucket, Key, Body, CacheControl))

    fake = FakeS3()
    cfg = replace(
        publish_module.export_config,
        publish_enabled=True,
        publish_backend="s3",
        publish_base_url="https://downloads.example.org/mydnaobv",
        publish_bucket="dna-downloads",
        publish_prefix="mydnaobv",
        publish_s3_endpoint="https://example.r2.cloudflarestorage.com",
        publish_s3_access_key_id="key",
        publish_s3_secret_access_key="secret",
    )
    monkeypatch.setattr(publish_module, "export_config", cfg)
    monkeypatch.setattr(publish_module, "_s3_client", lambda: fake)

    storage_root = tmp_path / "exports"
    source_dir = storage_root / "job_2" / "final"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "all_observations.pdf").write_bytes(b"pdf-bytes")

    job = SimpleNamespace(id=2, list_id=7, status="ready")
    artifacts = [
        SimpleNamespace(
            id=201,
            kind="merged_pdf",
            part_number=None,
            relative_path="job_2/final/all_observations.pdf",
        )
    ]

    warning = publish_module.publish_job_artifacts(job, artifacts, storage_root)
    assert warning is None
    assert fake.upload_calls == [
        (
            str(source_dir / "all_observations.pdf"),
            "dna-downloads",
            "mydnaobv/list_7/job_2/all_observations.pdf",
            {"CacheControl": publish_module.CACHE_CONTROL_IMMUTABLE},
        ),
        (
            str(source_dir / "all_observations.pdf"),
            "dna-downloads",
            "mydnaobv/list_7/latest/all_observations.pdf",
            {"CacheControl": publish_module.CACHE_CONTROL_LATEST},
        ),
    ]
    assert len(fake.put_calls) == 2


def test_latest_artifact_exists_s3_returns_true_when_enabled(monkeypatch):
    cfg = replace(
        publish_module.export_config,
        publish_enabled=True,
        publish_backend="s3",
        publish_base_url="https://downloads.example.org/mydnaobv",
        publish_bucket="dna-downloads",
        publish_s3_endpoint="https://example.r2.cloudflarestorage.com",
        publish_s3_access_key_id="key",
        publish_s3_secret_access_key="secret",
    )
    monkeypatch.setattr(publish_module, "export_config", cfg)

    artifact = SimpleNamespace(
        id=301,
        kind="merged_pdf",
        part_number=None,
        relative_path="job_3/final/all_observations.pdf",
    )
    assert publish_module.latest_artifact_exists(5, artifact) is True
