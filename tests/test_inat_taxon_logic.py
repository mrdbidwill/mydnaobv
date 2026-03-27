from app.services.inat import _extract_taxa


def test_extract_taxa_prefers_observer_current_identification_for_observation_taxon():
    obs = {
        "user": {"id": 10, "login": "observer"},
        "species_guess": "Old Guess",
        "taxon": {"id": 100, "name": "Communityus oldus", "rank": "species"},
        "community_taxon_id": 100,
        "identifications": [
            {
                "current": True,
                "own_observation": False,
                "user": {"id": 55, "login": "other"},
                "taxon": {"id": 101, "name": "Other taxon", "rank": "species"},
            },
            {
                "current": True,
                "own_observation": True,
                "user": {"id": 10, "login": "observer"},
                "taxon": {"id": 102, "name": "Observer taxon", "rank": "species"},
            },
        ],
    }

    out = _extract_taxa(obs)
    assert out["current_taxon_id"] == 100
    assert out["current_taxon_name"] == "Communityus oldus"
    assert out["observation_taxon_id"] == 102
    assert out["observation_taxon_name"] == "Observer taxon"
    assert out["community_taxon_id"] == 100
    assert out["community_taxon_name"] == "Communityus oldus"


def test_extract_taxa_falls_back_to_species_guess_when_no_identification_taxon():
    obs = {
        "user": {"id": 11, "login": "observer2"},
        "species_guess": "Fallback species guess",
        "identifications": [],
        "taxon": {},
    }

    out = _extract_taxa(obs)
    assert out["current_taxon_name"] is None
    assert out["observation_taxon_name"] == "Fallback species guess"
    assert out["community_taxon_name"] is None
