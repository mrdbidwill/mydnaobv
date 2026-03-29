from dataclasses import replace
from datetime import datetime, timedelta

from app.exports import service as export_service


def _cache_cfg(base, tmp_path):
    return replace(
        base,
        storage_dir=str(tmp_path),
        image_cache_enabled=True,
        image_cache_ttl_days=7,
        image_cache_retention_days=30,
        image_cache_prune_interval_hours=24,
        image_cache_max_prune_files=50,
    )


def test_image_cache_store_and_lookup_fresh(monkeypatch, tmp_path):
    cfg = _cache_cfg(export_service.export_config, tmp_path)
    monkeypatch.setattr(export_service, "export_config", cfg)

    now = datetime(2026, 3, 29, 12, 0, 0)
    url = "https://example.org/image_1.jpg"

    stored = export_service._store_image_cache_entry(
        image_url=url,
        payload=b"abc123",
        content_type="image/jpeg",
        now=now,
    )
    assert stored is not None
    assert stored.exists()

    cached_path, is_fresh = export_service._lookup_image_cache_path(url, now + timedelta(days=1))
    assert cached_path == stored
    assert is_fresh is True


def test_image_cache_lookup_marks_stale_after_ttl(monkeypatch, tmp_path):
    cfg = _cache_cfg(export_service.export_config, tmp_path)
    monkeypatch.setattr(export_service, "export_config", cfg)

    now = datetime(2026, 3, 29, 12, 0, 0)
    url = "https://example.org/image_2.jpg"

    export_service._store_image_cache_entry(
        image_url=url,
        payload=b"xyz987",
        content_type="image/jpeg",
        now=now,
    )

    cached_path, is_fresh = export_service._lookup_image_cache_path(url, now + timedelta(days=9))
    assert cached_path is not None
    assert cached_path.exists()
    assert is_fresh is False


def test_image_cache_prune_removes_old_entries(monkeypatch, tmp_path):
    cfg = _cache_cfg(export_service.export_config, tmp_path)
    monkeypatch.setattr(export_service, "export_config", cfg)

    now = datetime(2026, 3, 29, 12, 0, 0)
    url = "https://example.org/image_3.jpg"

    export_service._store_image_cache_entry(
        image_url=url,
        payload=b"prune-me",
        content_type="image/jpeg",
        now=now,
    )

    removed = export_service.prune_image_cache(now=now + timedelta(days=45), max_files=10)
    assert removed >= 1

    cached_path, _ = export_service._lookup_image_cache_path(url, now + timedelta(days=45))
    assert cached_path is None


def test_run_scheduled_maintenance_uses_interval_guard(monkeypatch, tmp_path):
    cfg = _cache_cfg(export_service.export_config, tmp_path)
    monkeypatch.setattr(export_service, "export_config", cfg)

    calls = {"cleanup": 0, "prune": 0}

    def fake_cleanup(_db):
        calls["cleanup"] += 1
        return 2

    def fake_prune(*, now, max_files):
        assert max_files == 50
        assert isinstance(now, datetime)
        calls["prune"] += 1
        return 3

    monkeypatch.setattr(export_service, "cleanup_expired_exports", fake_cleanup)
    monkeypatch.setattr(export_service, "prune_image_cache", fake_prune)

    first = export_service.run_scheduled_maintenance(db=None)
    assert first == {"removed_jobs": 2, "pruned_cache_files": 3}

    second = export_service.run_scheduled_maintenance(db=None)
    assert second == {"removed_jobs": 0, "pruned_cache_files": 0}
    assert calls == {"cleanup": 1, "prune": 1}
