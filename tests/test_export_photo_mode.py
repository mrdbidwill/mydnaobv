from dataclasses import replace
from types import SimpleNamespace

from app.exports import service as export_service


def _observation_with_photos():
    return SimpleNamespace(
        inat_observation_id=123,
        inat_url="https://www.inaturalist.org/observations/123",
        scientific_name="Test species",
        photo_url="https://example.com/primary.jpg",
        photo_license_code="cc-by",
        photo_attribution="Photo Author",
        photos=[
            SimpleNamespace(
                photo_index=1,
                id=1,
                photo_url="https://example.com/p1.jpg",
                photo_license_code="cc-by",
                photo_attribution="A",
            ),
            SimpleNamespace(
                photo_index=2,
                id=2,
                photo_url="https://example.com/p2.jpg",
                photo_license_code="cc-by-sa",
                photo_attribution="B",
            ),
            SimpleNamespace(
                photo_index=3,
                id=3,
                photo_url="https://example.com/p3.jpg",
                photo_license_code="cc0",
                photo_attribution="C",
            ),
        ],
    )


def test_primary_mode_returns_single_primary_candidate(monkeypatch):
    cfg = replace(export_service.export_config, include_all_photos=False)
    monkeypatch.setattr(export_service, "export_config", cfg)

    obs = _observation_with_photos()
    candidates = export_service._photo_candidates_for_observation(obs)
    assert len(candidates) == 1
    assert candidates[0]["url"] == "https://example.com/primary.jpg"


def test_all_photos_mode_caps_candidates_per_observation(monkeypatch):
    cfg = replace(
        export_service.export_config,
        include_all_photos=True,
        max_photos_per_observation=2,
    )
    monkeypatch.setattr(export_service, "export_config", cfg)

    obs = _observation_with_photos()
    candidates = export_service._photo_candidates_for_observation(obs)
    assert [c["url"] for c in candidates] == [
        "https://example.com/p1.jpg",
        "https://example.com/p2.jpg",
    ]


def test_indexed_item_title_prefixes_observation_number():
    obs = SimpleNamespace(
        inat_observation_id=456,
        taxon_name="Amanita suballiacea",
        observation_taxon_name=None,
        scientific_name=None,
        community_taxon_name=None,
        species_guess=None,
        common_name=None,
    )
    title = export_service._indexed_item_title(obs, 3, "photo 1/4")
    assert title.startswith("3. Amanita suballiacea")
    assert "(photo 1/4)" in title
