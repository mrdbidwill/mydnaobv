import app.services.inat as inat


def test_queryable_dna_field_name_returns_config_value(monkeypatch):
    monkeypatch.setattr(inat.settings, "inat_dna_field_name", "DNA Barcode ITS")
    assert inat._queryable_dna_field_name() == "DNA Barcode ITS"


def test_queryable_dna_field_name_repairs_malformed_env_value(monkeypatch):
    monkeypatch.setattr(inat.settings, "inat_dna_field_name", "DNA Barcode ITSADMIN_USERNAME=admin")
    assert inat._queryable_dna_field_name() == "DNA Barcode ITS"


def test_queryable_dna_field_name_handles_blank(monkeypatch):
    monkeypatch.setattr(inat.settings, "inat_dna_field_name", "")
    assert inat._queryable_dna_field_name() is None
