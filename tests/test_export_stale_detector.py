from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.exports import service as export_service


def test_stale_when_no_completed_export():
    obs_list = SimpleNamespace(last_sync_at=datetime(2026, 3, 5, 10, 0, 0))
    stale, reason = export_service.is_list_export_stale(obs_list, None)
    assert stale is True
    assert "No completed export" in reason


def test_stale_when_list_synced_after_last_export():
    obs_list = SimpleNamespace(last_sync_at=datetime(2026, 3, 5, 12, 0, 0))
    latest_job = SimpleNamespace(
        finished_at=datetime(2026, 3, 5, 11, 0, 0),
        created_at=datetime(2026, 3, 5, 10, 30, 0),
    )
    stale, reason = export_service.is_list_export_stale(obs_list, latest_job)
    assert stale is True
    assert "changed after the latest export" in reason


def test_not_stale_when_export_is_newer_than_last_sync():
    sync_at = datetime(2026, 3, 5, 11, 0, 0, tzinfo=UTC)
    obs_list = SimpleNamespace(last_sync_at=sync_at)
    latest_job = SimpleNamespace(
        finished_at=sync_at + timedelta(minutes=30),
        created_at=sync_at,
    )
    stale, reason = export_service.is_list_export_stale(obs_list, latest_job)
    assert stale is False
    assert "up to date" in reason
