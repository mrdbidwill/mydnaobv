from app.services.catalog import flatten_observation_payload
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
from app.services import catalog as catalog_service


def test_flatten_observation_payload_extracts_key_fields():
    obs = {
        "id": 123,
        "uri": "https://www.inaturalist.org/observations/123",
        "taxon": {"id": 55922, "name": "Mycena", "rank": "genus"},
        "community_taxon": {"id": 50814, "name": "Agaricomycetes", "rank": "class"},
        "community_taxon_id": 50814,
        "species_guess": "Mycena sp.",
        "user": {"login": "tester"},
        "quality_grade": "needs_id",
        "observed_on": "2026-03-28",
        "time_observed_at": "2026-03-28T08:30:00-05:00",
        "created_at": "2026-03-28T09:00:00-05:00",
        "updated_at": "2026-03-28T09:10:00-05:00",
        "place_guess": "Jefferson County, Alabama",
        "location": "33.50,-86.80",
        "photos": [
            {
                "url": "https://static.inaturalist.org/photos/1/medium.jpg",
                "license_code": "cc-by",
                "attribution": "(c) user",
            }
        ],
    }

    out = flatten_observation_payload(obs)
    assert out is not None
    assert out["inat_observation_id"] == 123
    assert out["taxon_name"] == "Mycena"
    assert out["community_taxon_name"] == "Agaricomycetes"
    assert out["genus_key"] == "mycena"
    assert out["user_login"] == "tester"
    assert out["photo_count"] == 1
    assert out["primary_photo_url"] == "https://static.inaturalist.org/photos/1/medium.jpg"
    assert out["has_dna_its"] is False


def test_flatten_observation_payload_fallbacks_to_species_guess_for_genus():
    obs = {
        "id": 456,
        "species_guess": "cf. Agaricus campestris",
        "photos": [],
    }
    out = flatten_observation_payload(obs)
    assert out is not None
    assert out["genus_key"] == "agaricus"


def test_flatten_observation_payload_marks_dna_its_when_field_present(monkeypatch):
    monkeypatch.setattr(catalog_service.settings, "inat_dna_field_id", "2330")
    obs = {
        "id": 789,
        "ofvs": [{"observation_field_id": 2330, "value": "ITS-789"}],
        "photos": [],
    }
    out = flatten_observation_payload(obs)
    assert out is not None
    assert out["has_dna_its"] is True


def test_sync_catalog_source_persists_links_before_orphan_cleanup(monkeypatch):
    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self._calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None):
            self._calls += 1
            page = int((params or {}).get("page", 1))
            if page == 1:
                return _FakeResponse(
                    {
                        "total_results": 2,
                        "results": [
                            {
                                "id": 1001,
                                "uri": "https://www.inaturalist.org/observations/1001",
                                "taxon": {"id": 1, "name": "Agaricus campestris", "rank": "species"},
                                "species_guess": "Agaricus campestris",
                                "photos": [],
                            },
                            {
                                "id": 1002,
                                "uri": "https://www.inaturalist.org/observations/1002",
                                "taxon": {"id": 2, "name": "Boletus edulis", "rank": "species"},
                                "species_guess": "Boletus edulis",
                                "photos": [],
                            },
                        ],
                    }
                )
            return _FakeResponse({"total_results": 2, "results": []})

    monkeypatch.setattr(catalog_service.httpx, "Client", _FakeClient)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    db = TestingSession()
    try:
        source = models.CatalogSource(project_id="test-project", is_active=True)
        db.add(source)
        db.commit()
        db.refresh(source)

        summary = catalog_service.sync_catalog_source(db, source, max_pages=2)
        assert summary["inserted"] == 2
        assert summary["linked"] == 2

        link_count = db.query(models.CatalogObservationProject).filter_by(source_id=source.id).count()
        obs_count = db.query(models.CatalogObservation).count()
        assert link_count == 2
        assert obs_count == 2
    finally:
        db.close()
