from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
import app.main as main


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    return TestingSession()


def _seed_public_artifact(db, *, kind: str = "observations_index_pdf") -> tuple[int, int]:
    now = datetime.now(UTC).replace(tzinfo=None)
    obs_list = models.ObservationList(
        id=1,
        title="Baldwin County",
        product_type="county",
        is_public_download=True,
        inat_dna_field_id="2330",
        created_at=now,
    )
    job = models.ExportJob(
        id=10,
        list_id=1,
        status="ready",
        phase="done",
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    artifact = models.ExportArtifact(
        id=20,
        job_id=10,
        kind=kind,
        relative_path=f"job_10/final/{kind}.bin",
        size_bytes=123,
        created_at=now,
    )
    db.add(obs_list)
    db.add(job)
    db.add(artifact)
    db.commit()
    return obs_list.id, artifact.id


def test_public_download_legacy_no_marker_redirects_to_latest(monkeypatch):
    db = _session()
    try:
        list_id, artifact_id = _seed_public_artifact(db, kind="observations_index_pdf")

        monkeypatch.setattr(main, "artifact_abspath", lambda _artifact: Path("/tmp/not-found-public-artifact"))
        monkeypatch.setattr(main, "latest_artifact_exists", lambda _list_id, _artifact: False)
        monkeypatch.setattr(main, "has_latest_publish_marker", lambda _list_id: False)
        monkeypatch.setattr(
            main,
            "published_latest_url",
            lambda _list_id, _artifact: "https://downloads.example.org/list_1/latest/index.pdf?v=20",
        )

        response = main.public_download_latest_artifact(list_id=list_id, artifact_id=artifact_id, db=db)
        assert response.status_code == 307
        assert response.headers.get("location") == "https://downloads.example.org/list_1/latest/index.pdf?v=20"
    finally:
        db.close()


def test_public_download_zip_chunk_no_marker_still_404(monkeypatch):
    db = _session()
    try:
        list_id, artifact_id = _seed_public_artifact(db, kind="zip_chunk")

        monkeypatch.setattr(main, "artifact_abspath", lambda _artifact: Path("/tmp/not-found-public-artifact"))
        monkeypatch.setattr(main, "latest_artifact_exists", lambda _list_id, _artifact: False)
        monkeypatch.setattr(main, "has_latest_publish_marker", lambda _list_id: False)
        monkeypatch.setattr(
            main,
            "published_latest_url",
            lambda _list_id, _artifact: "https://downloads.example.org/list_1/latest/parts.zip.part001?v=20",
        )

        with pytest.raises(main.HTTPException) as exc:
            main.public_download_latest_artifact(list_id=list_id, artifact_id=artifact_id, db=db)
        assert exc.value.status_code == 404
        assert exc.value.detail == "File not available"
    finally:
        db.close()
