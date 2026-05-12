from app.exports import worker


class _DummyDB:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_run_once_skips_housekeeping_when_lock_not_acquired(monkeypatch):
    db = _DummyDB()
    calls = {
        "maintenance": 0,
        "enqueue": 0,
        "process": 0,
        "publish": 0,
        "release": 0,
    }
    process_allow_sync_values = []
    released_handles = []

    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "_try_acquire_housekeeping_lock", lambda: None)

    def release_stub(handle):
        calls["release"] += 1
        released_handles.append(handle)

    monkeypatch.setattr(worker, "_release_housekeeping_lock", release_stub)
    monkeypatch.setattr(
        worker,
        "run_scheduled_maintenance",
        lambda _db: calls.__setitem__("maintenance", calls["maintenance"] + 1),
    )
    monkeypatch.setattr(
        worker,
        "enqueue_due_public_refresh_jobs",
        lambda _db, limit: calls.__setitem__("enqueue", calls["enqueue"] + 1),
    )
    monkeypatch.setattr(
        worker,
        "process_next_job",
        lambda _db, **kwargs: (
            process_allow_sync_values.append(bool(kwargs.get("allow_force_sync_plan"))),
            calls.__setitem__("process", calls["process"] + 1),
        )[-1],
    )
    monkeypatch.setattr(
        worker,
        "process_pending_publish_jobs",
        lambda _db, limit: calls.__setitem__("publish", calls["publish"] + 1),
    )

    assert worker.run_once() == 0
    assert calls["maintenance"] == 0
    assert calls["enqueue"] == 0
    assert calls["process"] == 1
    assert calls["publish"] == 1
    assert calls["release"] == 1
    assert released_handles == [None]
    assert process_allow_sync_values == [False]
    assert db.closed is True


def test_run_once_runs_housekeeping_when_lock_acquired(monkeypatch):
    db = _DummyDB()
    lock_token = object()
    calls = {
        "maintenance": 0,
        "enqueue": 0,
        "process": 0,
        "publish": 0,
        "release": 0,
    }
    process_allow_sync_values = []
    released_handles = []

    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "_try_acquire_housekeeping_lock", lambda: lock_token)

    def release_stub(handle):
        calls["release"] += 1
        released_handles.append(handle)

    monkeypatch.setattr(worker, "_release_housekeeping_lock", release_stub)
    monkeypatch.setattr(
        worker,
        "run_scheduled_maintenance",
        lambda _db: calls.__setitem__("maintenance", calls["maintenance"] + 1),
    )
    monkeypatch.setattr(
        worker,
        "enqueue_due_public_refresh_jobs",
        lambda _db, limit: calls.__setitem__("enqueue", calls["enqueue"] + 1),
    )
    monkeypatch.setattr(
        worker,
        "process_next_job",
        lambda _db, **kwargs: (
            process_allow_sync_values.append(bool(kwargs.get("allow_force_sync_plan"))),
            calls.__setitem__("process", calls["process"] + 1),
        )[-1],
    )
    monkeypatch.setattr(
        worker,
        "process_pending_publish_jobs",
        lambda _db, limit: calls.__setitem__("publish", calls["publish"] + 1),
    )

    assert worker.run_once() == 0
    assert calls["maintenance"] == 1
    assert calls["enqueue"] == 1
    assert calls["process"] == 1
    assert calls["publish"] == 1
    assert calls["release"] == 1
    assert released_handles == [lock_token]
    assert process_allow_sync_values == [True]
    assert db.closed is True
