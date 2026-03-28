import pytest
from fastapi import HTTPException

from app.main import (
    _build_project_overlap_summary,
    _extract_genus_token,
    ensure_data_catalog_enabled,
    normalize_catalog_sort,
    settings,
)


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


def test_build_project_overlap_summary_reports_pairs_and_exact_groups():
    links = [
        (101, 1),
        (101, 2),
        (102, 1),
        (102, 2),
        (102, 3),
        (103, 2),
        (104, 3),
    ]
    labels = {1: "Project 1", 2: "Project 2", 3: "Project 3"}

    summary = _build_project_overlap_summary(links, labels)

    assert summary["total_multi_project_observations"] == 2

    per_source = {row["source_id"]: row["duplicate_count"] for row in summary["per_source_rows"]}
    assert per_source == {1: 2, 2: 2, 3: 1}

    pair_counts = {row["pair_label"]: row["count"] for row in summary["pair_rows"]}
    assert pair_counts["Project 1 + Project 2"] == 2
    assert pair_counts["Project 1 + Project 3"] == 1
    assert pair_counts["Project 2 + Project 3"] == 1

    exact_counts = {row["combo_label"]: row["count"] for row in summary["exact_rows"]}
    assert exact_counts["Project 1 + Project 2"] == 1
    assert exact_counts["Project 1 + Project 2 + Project 3"] == 1
