import pytest
from fastapi import HTTPException

from app.main import _alpha_initial, _catalog_genus_label, _payload_has_dna_its, ensure_data_catalog_enabled, normalize_catalog_sort, settings


def test_normalize_catalog_sort_defaults_on_unknown():
    assert normalize_catalog_sort("") == "observed_desc"
    assert normalize_catalog_sort("unknown") == "observed_desc"


def test_normalize_catalog_sort_accepts_known_values():
    assert normalize_catalog_sort("observed_desc") == "observed_desc"
    assert normalize_catalog_sort("observed_asc") == "observed_asc"
    assert normalize_catalog_sort("genus_asc") == "genus_asc"
    assert normalize_catalog_sort("taxon_asc") == "taxon_asc"
    assert normalize_catalog_sort("community_taxon_asc") == "community_taxon_asc"
    assert normalize_catalog_sort("observed_taxon_asc") == "observed_taxon_asc"
    assert normalize_catalog_sort("place_asc") == "place_asc"
    assert normalize_catalog_sort("updated_desc") == "updated_desc"


def test_ensure_data_catalog_enabled_allows_enabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_data_catalog", True)
    ensure_data_catalog_enabled()


def test_ensure_data_catalog_enabled_raises_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_data_catalog", False)
    with pytest.raises(HTTPException) as exc:
        ensure_data_catalog_enabled()
    assert exc.value.status_code == 404


def test_catalog_genus_label_prefers_taxon_and_falls_back():
    assert _catalog_genus_label("Agaricus campestris", None, None, None) == "Agaricus"
    assert _catalog_genus_label(None, "cf. Trametes versicolor", None, None) == "Trametes"
    assert _catalog_genus_label(None, None, None, "boletus") == "boletus"


def test_alpha_initial_handles_letters_and_non_letters():
    assert _alpha_initial("Agaricus") == "A"
    assert _alpha_initial(" 9-lives ") == "#"


def test_payload_has_dna_its_true_when_field_id_2330_has_value():
    payload = '{"ofvs":[{"observation_field_id":2330,"value":"ITS123"}]}'
    assert _payload_has_dna_its(payload) is True


def test_payload_has_dna_its_false_when_field_missing_or_empty():
    assert _payload_has_dna_its('{"ofvs":[{"observation_field_id":2330,"value":""}]}') is False
    assert _payload_has_dna_its('{"ofvs":[{"observation_field_id":9999,"value":"x"}]}') is False
