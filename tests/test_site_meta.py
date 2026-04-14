from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
import app.main as main


def _request(path: str) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 123),
        "server": ("dna.mrdbid.com", 443),
        "root_path": "",
    }
    return Request(scope)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    return testing_session()


def _seed_public_artifact(db):
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
        kind="observations_index_pdf",
        relative_path="job_10/final/observations_index.pdf",
        size_bytes=123,
        created_at=now,
    )
    db.add(obs_list)
    db.add(job)
    db.add(artifact)
    db.commit()


def _restore_fields_set(original: set[str]) -> None:
    main.settings.model_fields_set.clear()
    main.settings.model_fields_set.update(original)


def test_adsense_enabled_defaults_true_in_production_when_unset(monkeypatch):
    original = set(main.settings.model_fields_set)
    try:
        main.settings.model_fields_set.discard("adsense_enabled")
        monkeypatch.setattr(main.settings, "env", "production")
        assert main._adsense_enabled_for_runtime() is True
    finally:
        _restore_fields_set(original)


def test_adsense_enabled_respects_explicit_config(monkeypatch):
    original = set(main.settings.model_fields_set)
    try:
        main.settings.model_fields_set.add("adsense_enabled")
        monkeypatch.setattr(main.settings, "env", "production")
        monkeypatch.setattr(main.settings, "adsense_enabled", False)
        assert main._adsense_enabled_for_runtime() is False
    finally:
        _restore_fields_set(original)


def test_ads_txt_returns_publisher_line(monkeypatch):
    monkeypatch.setattr(main.settings, "adsense_client_id", "ca-pub-1111222233334444")
    response = main.ads_txt()
    assert response.body.decode("utf-8") == "google.com, pub-1111222233334444, DIRECT, f08c47fec0942fa0"


def test_robots_txt_includes_sitemap_url():
    response = main.robots_txt(_request("/robots.txt"))
    body = response.body.decode("utf-8")
    assert "User-agent: *" in body
    assert "Disallow: /admin" in body
    assert "Sitemap: https://dna.mrdbid.com/sitemap.xml" in body


def test_sitemap_entries_include_public_artifact_url(monkeypatch):
    db = _session()
    try:
        _seed_public_artifact(db)
        monkeypatch.setattr(main.settings, "enable_data_catalog", False)
        entries = main._sitemap_entries(_request("/sitemap.xml"), db)
        assert "https://dna.mrdbid.com/" in entries
        assert "https://dna.mrdbid.com/methodology" in entries
        assert "https://dna.mrdbid.com/public/lists/1/artifacts/20/download" in entries
    finally:
        db.close()
