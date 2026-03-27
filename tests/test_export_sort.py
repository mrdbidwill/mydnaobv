from app import models
from app.core.config import settings
from app.exports.service import (
    _build_genera_count_lines,
    _extract_genus_key,
    _observation_genus_sort_key,
    _preferred_taxon_title,
)
from app.exports.pdf_writer import _observation_index_title


def test_extract_genus_key_skips_common_qualifiers():
    assert _extract_genus_key("cf. Amanita muscaria") == "amanita"
    assert _extract_genus_key("aff Boletus sp.") == "boletus"
    assert _extract_genus_key("Cantharellus cibarius") == "cantharellus"


def test_observation_sort_key_orders_by_genus_then_title():
    observations = [
        models.Observation(
            list_id=1,
            inat_observation_id=3,
            inat_url="https://www.inaturalist.org/observations/3",
            scientific_name="Boletus edulis",
        ),
        models.Observation(
            list_id=1,
            inat_observation_id=1,
            inat_url="https://www.inaturalist.org/observations/1",
            scientific_name="Agaricus campestris",
        ),
        models.Observation(
            list_id=1,
            inat_observation_id=2,
            inat_url="https://www.inaturalist.org/observations/2",
            scientific_name="Agaricus arvensis",
        ),
    ]

    sorted_obs = sorted(observations, key=_observation_genus_sort_key)
    assert [obs.inat_observation_id for obs in sorted_obs] == [2, 1, 3]


def test_preferred_taxon_title_uses_configured_source():
    obs = models.Observation(
        list_id=1,
        inat_observation_id=10,
        taxon_name="Mycena",
        observation_taxon_name="Agaricomycetes",
        scientific_name="Agaricomycetes",
        community_taxon_name="Agaricomycetes",
    )
    assert _preferred_taxon_title(obs, sort_source="observation") == "Agaricomycetes"
    assert _preferred_taxon_title(obs, sort_source="taxon") == "Mycena"


def test_sort_key_can_use_taxon_source():
    observations = [
        models.Observation(
            list_id=1,
            inat_observation_id=1,
            taxon_name="Trametes versicolor",
            observation_taxon_name="Agaricomycetes",
        ),
        models.Observation(
            list_id=1,
            inat_observation_id=2,
            taxon_name="Boletus edulis",
            observation_taxon_name="Agaricomycetes",
        ),
    ]
    sorted_obs = sorted(observations, key=lambda obs: _observation_genus_sort_key(obs, sort_source="taxon"))
    assert [obs.inat_observation_id for obs in sorted_obs] == [2, 1]


def test_observation_index_title_prefers_taxon_when_source_is_taxon(monkeypatch):
    obs = models.Observation(
        list_id=1,
        inat_observation_id=11,
        taxon_name="Mycena",
        observation_taxon_name="Agaricomycetes",
    )
    monkeypatch.setattr(settings, "export_sort_taxon_source", "taxon")
    assert _observation_index_title(obs) == "Mycena"


def test_build_genera_count_lines_counts_and_orders():
    observations = [
        models.Observation(list_id=1, inat_observation_id=1, observation_taxon_name="Agaricales"),
        models.Observation(list_id=1, inat_observation_id=2, observation_taxon_name="Ascomycota"),
        models.Observation(list_id=1, inat_observation_id=3, observation_taxon_name="Agaricales"),
        models.Observation(list_id=1, inat_observation_id=4, observation_taxon_name="cf. Ascomycota"),
        models.Observation(list_id=1, inat_observation_id=5, observation_taxon_name="  "),
    ]
    lines = _build_genera_count_lines(observations, sort_source="observation")
    assert lines == [
        "1. Agaricales (2)",
        "2. Ascomycota (2)",
    ]
