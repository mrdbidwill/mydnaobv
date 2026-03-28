import pytest
from fastapi import HTTPException

from app.main import _extract_genus_token, ensure_data_catalog_enabled, normalize_catalog_sort, settings


def test_normalize_catalog_sort_defaults_on_unknown():
    assert normalize_catalog_sort("") == "observed_desc"
    assert normalize_catalog_sort("unknown") == "observed_desc"


def test_normalize_catalog_sort_accepts_known_values():
    assert normalize_catalog_sort("observed_desc") == "observed_desc"
    assert normalize_catalog_sort("observed_asc") == "observed_asc"
    assert normalize_catalog_sort("genus_asc") == "genus_asc"
    assert normalize_catalog_sort("taxon_asc") == "taxon_asc"
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


def test_extract_genus_token_skips_qualifier_tokens():
    assert _extract_genus_token("cf. Agaricus campestris") == "agaricus"
    assert _extract_genus_token("sp. Trametes") == "trametes"
