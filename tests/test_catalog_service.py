from app.services.catalog import flatten_observation_payload


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


def test_flatten_observation_payload_fallbacks_to_species_guess_for_genus():
    obs = {
        "id": 456,
        "species_guess": "cf. Agaricus campestris",
        "photos": [],
    }
    out = flatten_observation_payload(obs)
    assert out is not None
    assert out["genus_key"] == "agaricus"
