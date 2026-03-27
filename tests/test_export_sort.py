from app import models
from app.exports.service import _extract_genus_key, _observation_genus_sort_key, _preferred_taxon_title


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
