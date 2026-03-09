from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import app.main as main
from app.main import _artifact_by_kind, _preferred_county_file_artifact, _refresh_summary


def test_preferred_county_artifact_prioritizes_merged_pdf():
    artifacts = [
        SimpleNamespace(kind="zip"),
        SimpleNamespace(kind="merged_pdf"),
    ]
    chosen = _preferred_county_file_artifact(artifacts)
    assert chosen is not None
    assert chosen.kind == "merged_pdf"


def test_artifact_by_kind_finds_observation_index_pdf():
    artifacts = [
        SimpleNamespace(kind="merged_pdf"),
        SimpleNamespace(kind="observations_index_pdf"),
    ]
    chosen = _artifact_by_kind(artifacts, "observations_index_pdf")
    assert chosen is not None
    assert chosen.kind == "observations_index_pdf"


def test_refresh_summary_due_when_missing_sync():
    payload = _refresh_summary(None)
    assert payload["is_due"] is True
    assert payload["last_refreshed_label"] == "Not refreshed yet"


def test_refresh_summary_not_due_for_recent_sync():
    recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    payload = _refresh_summary(recent)
    assert payload["is_due"] is False
    assert payload["last_refreshed_label"] != "Not refreshed yet"


def test_configured_public_states_parses_csv(monkeypatch):
    monkeypatch.setattr(main.settings, "public_state_codes", "AL, GA")
    assert main._configured_public_states() == {"AL", "GA"}


def test_configured_public_states_all_keyword(monkeypatch):
    monkeypatch.setattr(main.settings, "public_state_codes", "ALL")
    states = main._configured_public_states()
    assert "AL" in states
    assert "TX" in states
