import app.services.inat as inat
from types import SimpleNamespace


def test_queryable_dna_field_name_returns_config_value(monkeypatch):
    monkeypatch.setattr(inat.settings, "inat_dna_field_name", "DNA Barcode ITS")
    assert inat._queryable_dna_field_name() == "DNA Barcode ITS"


def test_queryable_dna_field_name_repairs_malformed_env_value(monkeypatch):
    monkeypatch.setattr(inat.settings, "inat_dna_field_name", "DNA Barcode ITSADMIN_USERNAME=admin")
    assert inat._queryable_dna_field_name() == "DNA Barcode ITS"


def test_queryable_dna_field_name_handles_blank(monkeypatch):
    monkeypatch.setattr(inat.settings, "inat_dna_field_name", "")
    assert inat._queryable_dna_field_name() is None


def test_split_project_filter_values_dedupes_and_ignores_blanks():
    assert inat._split_project_filter_values("124358, 184305\n132913,124358,, ") == [
        "124358",
        "184305",
        "132913",
    ]


def test_project_filters_for_county_prefers_configured_set(monkeypatch):
    monkeypatch.setattr(inat.settings, "inat_county_project_ids", "124358,184305,132913,251751")
    obs_list = SimpleNamespace(product_type="county", inat_project_id="184305")
    assert inat._project_filters_for_list(obs_list) == ["124358", "184305", "132913", "251751"]


def test_project_filters_for_non_county_use_list_value():
    obs_list = SimpleNamespace(product_type="project", inat_project_id="251751")
    assert inat._project_filters_for_list(obs_list) == ["251751"]
