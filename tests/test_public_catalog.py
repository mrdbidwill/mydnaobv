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


def test_artifact_public_download_url_builds_download_query():
    artifact = SimpleNamespace(id=20)
    url = main._artifact_public_download_url(7, artifact)
    assert url == "/public/lists/7/artifacts/20/download?download=1"


def test_artifact_public_url_uses_app_route_even_when_latest_marker_exists(monkeypatch):
    artifact = SimpleNamespace(id=20, kind="observations_index_pdf")
    monkeypatch.setattr(main, "latest_artifact_exists", lambda _list_id, _artifact: True)
    monkeypatch.setattr(main, "published_latest_url", lambda _list_id, _artifact: "https://downloads.example.org/x.pdf")
    url = main._artifact_public_url(7, artifact)
    assert url == "/public/lists/7/artifacts/20/download"


def test_refresh_summary_due_when_missing_sync():
    payload = _refresh_summary(None)
    assert payload["is_due"] is True
    assert payload["last_refreshed_label"] == "Not refreshed yet"


def test_refresh_summary_not_due_for_recent_sync():
    recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    payload = _refresh_summary(recent)
    assert payload["is_due"] is False
    assert payload["last_refreshed_label"] != "Not refreshed yet"


def test_refresh_summary_due_with_active_throttle_retry_note():
    stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    active_job = SimpleNamespace(
        status="waiting_quota",
        next_run_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30),
        message="Sync paused by iNaturalist throttling (HTTP 429). backoff_attempt=2.",
    )
    payload = _refresh_summary(stale, active_refresh_job=active_job)
    assert payload["is_due"] is True
    assert "Refresh is delayed" in payload["status_line"]
    assert "next retry" not in payload["status_line"]


def test_refresh_summary_due_with_cached_defer_note():
    stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    latest_completed_job = SimpleNamespace(
        message=(
            "Sync deferred (iNaturalist throttling HTTP 429 backoff_attempt=1). "
            "Proceeding with cached observations."
        )
    )
    payload = _refresh_summary(stale, latest_completed_job=latest_completed_job)
    assert payload["is_due"] is True
    assert "cached observations" in payload["status_line"]


def test_refresh_summary_due_with_inferred_cached_update_note():
    stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    latest_completed_job = SimpleNamespace(
        message="Export complete: observations index PDF and ZIP with split county guide parts ready.",
        finished_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
        started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1, minutes=10),
    )
    payload = _refresh_summary(stale, latest_completed_job=latest_completed_job)
    assert payload["is_due"] is True
    assert "completed from cached observations" in payload["status_line"]


def test_configured_public_states_parses_csv(monkeypatch):
    monkeypatch.setattr(main.settings, "public_state_codes", "AL, GA")
    assert main._configured_public_states() == {"AL", "GA"}


def test_configured_public_states_all_keyword(monkeypatch):
    monkeypatch.setattr(main.settings, "public_state_codes", "ALL")
    states = main._configured_public_states()
    assert "AL" in states
    assert "TX" in states


def test_project_reference_lookup_by_numeric_id():
    payload = main._project_reference("184305")
    assert payload is not None
    assert payload["name"] == "Fungi of Alabama- AMS FunDiS Local Project"
    assert "snapshot_label" in payload


def test_project_reference_lookup_with_non_numeric_text():
    payload = main._project_reference("project #132913")
    assert payload is not None
    assert "With DNA Barcode ITS" in payload["stats_dna"]
