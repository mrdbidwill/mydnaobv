from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
from app.exports import publish as publish_module
from app.exports import service as export_service


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    return TestingSession()


def _mk_list(db, list_id: int, *, product_type: str = "county", is_public: bool = True) -> models.ObservationList:
    row = models.ObservationList(
        id=list_id,
        title=f"List {list_id}",
        product_type=product_type,
        is_public_download=is_public,
        inat_dna_field_id="2330",
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(row)
    db.commit()
    return row


def _mk_observation(db, list_id: int, inat_id: int = 999, *, with_photo: bool = True) -> models.Observation:
    row = models.Observation(
        list_id=list_id,
        inat_observation_id=inat_id,
        inat_url=f"https://www.inaturalist.org/observations/{inat_id}",
        scientific_name="Testus species",
        photo_url="https://example.com/photo.jpg" if with_photo else None,
        photo_license_code="cc-by" if with_photo else None,
        photo_attribution="Tester" if with_photo else None,
    )
    db.add(row)
    db.commit()
    return row


def test_pick_next_job_skips_fresh_running_jobs():
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        _mk_list(db, 1)
        db.add(
            models.ExportJob(
                list_id=1,
                status="running",
                phase="download",
                updated_at=now,
                created_at=now - timedelta(minutes=2),
                next_run_at=now - timedelta(minutes=1),
            )
        )
        queued = models.ExportJob(
            list_id=1,
            status="queued",
            phase="plan",
            updated_at=now,
            created_at=now - timedelta(minutes=1),
            next_run_at=now - timedelta(minutes=1),
        )
        db.add(queued)
        db.commit()

        picked = export_service._pick_next_job(db, now)
        assert picked is not None
        assert picked.id == queued.id
        assert picked.status == "queued"
    finally:
        db.close()


def test_requeue_stale_running_jobs():
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        _mk_list(db, 2)
        stale = models.ExportJob(
            list_id=2,
            status="running",
            phase="download",
            updated_at=now - timedelta(minutes=20),
            created_at=now - timedelta(minutes=30),
            next_run_at=now - timedelta(minutes=10),
            message="syncing",
        )
        db.add(stale)
        db.commit()

        requeued = export_service._requeue_stale_running_jobs(db, now)
        db.refresh(stale)
        assert requeued == 1
        assert stale.status == "queued"
        assert stale.next_run_at is not None
        assert stale.next_run_at <= now
        assert "Recovered stale running job lock." in (stale.message or "")
    finally:
        db.close()


def test_process_next_job_rolls_back_and_marks_failed(monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        _mk_list(db, 3)
        job = models.ExportJob(
            list_id=3,
            status="queued",
            phase="plan",
            updated_at=now,
            created_at=now,
            next_run_at=now - timedelta(seconds=1),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(export_service.export_config, enabled=True),
        )
        monkeypatch.setattr(
            export_service,
            "_process_phase",
            lambda _db, _job, _deadline: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        out = export_service.process_next_job(db)
        assert out is not None
        assert out.id == job.id
        assert out.status == "failed"
        assert out.phase == "done"
        assert "worker_error: boom" in (out.message or "")
    finally:
        db.close()


def test_enqueue_due_public_refresh_jobs_includes_project_lists():
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        county = _mk_list(db, 10, product_type="county", is_public=True)
        project = _mk_list(db, 11, product_type="project", is_public=True)
        county.last_sync_at = now - timedelta(days=20)
        project.last_sync_at = now - timedelta(days=20)
        db.commit()

        queued = export_service.enqueue_due_public_refresh_jobs(db, limit=10)
        assert queued == 2

        rows = (
            db.query(models.ExportJob.list_id, models.ExportJob.requested_by, models.ExportJob.force_sync)
            .order_by(models.ExportJob.list_id.asc())
            .all()
        )
        assert rows == [
            (county.id, "auto-refresh", True),
            (project.id, "auto-refresh", True),
        ]
    finally:
        db.close()


def test_enqueue_due_public_refresh_jobs_skips_recent_sync_defer_completion(monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        project = _mk_list(db, 16, product_type="project", is_public=True)
        project.last_sync_at = now - timedelta(days=20)
        db.add(
            models.ExportJob(
                list_id=project.id,
                status="ready",
                phase="done",
                message=(
                    "Sync deferred (iNaturalist throttling HTTP 429 backoff_attempt=1). "
                    "Proceeding with 42 cached observations from last sync at 2026-03-27T12:12:12.265356 UTC."
                ),
                created_at=now - timedelta(minutes=10),
                updated_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=10),
            )
        )
        db.commit()

        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(export_service.export_config, sync_defer_retry_minutes=180),
        )

        queued = export_service.enqueue_due_public_refresh_jobs(db, limit=10)
        assert queued == 0

        active_auto = (
            db.query(models.ExportJob)
            .filter(
                models.ExportJob.list_id == project.id,
                models.ExportJob.requested_by == "auto-refresh",
            )
            .all()
        )
        assert active_auto == []
    finally:
        db.close()


def test_enqueue_due_public_refresh_jobs_retries_after_sync_defer_cooldown(monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        project = _mk_list(db, 17, product_type="project", is_public=True)
        project.last_sync_at = now - timedelta(days=20)
        db.add(
            models.ExportJob(
                list_id=project.id,
                status="ready",
                phase="done",
                message=(
                    "Sync deferred (iNaturalist throttling HTTP 429 backoff_attempt=1). "
                    "Proceeding with 84 cached observations from last sync at 2026-03-27T12:12:12.265356 UTC."
                ),
                created_at=now - timedelta(hours=8),
                updated_at=now - timedelta(hours=8),
                finished_at=now - timedelta(hours=8),
            )
        )
        db.commit()

        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(export_service.export_config, sync_defer_retry_minutes=120),
        )

        queued = export_service.enqueue_due_public_refresh_jobs(db, limit=10)
        assert queued == 1

        active_auto = (
            db.query(models.ExportJob)
            .filter(
                models.ExportJob.list_id == project.id,
                models.ExportJob.requested_by == "auto-refresh",
            )
            .order_by(models.ExportJob.id.desc())
            .first()
        )
        assert active_auto is not None
        assert active_auto.force_sync is True
        assert active_auto.status == "queued"
    finally:
        db.close()


def test_enqueue_due_public_refresh_jobs_skips_recent_unsynced_completion_without_marker(monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        project = _mk_list(db, 18, product_type="project", is_public=True)
        project.last_sync_at = now - timedelta(days=20)
        db.add(
            models.ExportJob(
                list_id=project.id,
                status="partial_ready",
                phase="done",
                message="Export complete: observations index PDF and ZIP with split county guide parts ready.",
                created_at=now - timedelta(minutes=12),
                started_at=now - timedelta(minutes=12),
                updated_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=10),
            )
        )
        db.commit()

        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(export_service.export_config, sync_defer_retry_minutes=180),
        )

        queued = export_service.enqueue_due_public_refresh_jobs(db, limit=10)
        assert queued == 0
    finally:
        db.close()


def test_phase_plan_marks_waiting_quota_on_sync_429(monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        _mk_list(db, 12, product_type="project", is_public=True)
        job = models.ExportJob(
            list_id=12,
            status="queued",
            phase="plan",
            force_sync=True,
            updated_at=now,
            created_at=now,
            next_run_at=now - timedelta(seconds=1),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        request = httpx.Request("GET", "https://api.inaturalist.org/v1/observations?page=2")
        response = httpx.Response(429, request=request, headers={"Retry-After": "1200"})
        throttle_error = httpx.HTTPStatusError("normal_throttling", request=request, response=response)

        monkeypatch.setattr(
            export_service,
            "sync_list_observations",
            lambda _db, _list: (_ for _ in ()).throw(throttle_error),
        )
        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(
                export_service.export_config,
                sync_backoff_jitter_ratio=0.0,
                sync_backoff_max_seconds=7200,
                sync_max_concurrent=1,
            ),
        )

        progressed = export_service._phase_plan(db, job)
        assert progressed is False
        assert job.status == "waiting_quota"
        assert job.phase == "plan"
        assert "HTTP 429" in (job.message or "")
        assert "backoff_attempt=1" in (job.message or "")
        assert job.next_run_at is not None
        assert job.next_run_at > now
        assert job.finished_at is None
        assert job.force_sync is True
    finally:
        db.close()


def test_phase_plan_waits_when_sync_slot_is_full(tmp_path, monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        _mk_list(db, 13, product_type="project", is_public=True)
        job = models.ExportJob(
            list_id=13,
            status="queued",
            phase="plan",
            force_sync=True,
            updated_at=now,
            created_at=now,
            next_run_at=now - timedelta(seconds=1),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(
                export_service.export_config,
                storage_dir=str(tmp_path),
                sync_max_concurrent=1,
                sync_slot_retry_seconds=90,
            ),
        )

        slot = export_service._try_acquire_sync_slot(1)
        assert slot is not None
        try:
            progressed = export_service._phase_plan(db, job)
        finally:
            slot.release()

        assert progressed is False
        assert job.status == "waiting_quota"
        assert job.phase == "plan"
        assert "global iNaturalist sync slot" in (job.message or "")
        assert job.next_run_at is not None
        assert job.next_run_at > now
        assert job.force_sync is True
    finally:
        db.close()


def test_phase_plan_defers_sync_slot_when_cache_exists(tmp_path, monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        _mk_list(db, 14, product_type="project", is_public=True)
        _mk_observation(db, 14, 14001, with_photo=True)
        job = models.ExportJob(
            list_id=14,
            status="queued",
            phase="plan",
            force_sync=True,
            updated_at=now,
            created_at=now,
            next_run_at=now - timedelta(seconds=1),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(
                export_service.export_config,
                storage_dir=str(tmp_path),
                sync_max_concurrent=1,
                sync_slot_retry_seconds=90,
                sync_defer_to_cache_products_csv="project",
            ),
        )

        slot = export_service._try_acquire_sync_slot(1)
        assert slot is not None
        try:
            progressed = export_service._phase_plan(db, job)
            assert progressed is True
            progressed = export_service._phase_plan(db, job)
        finally:
            slot.release()

        assert progressed is True
        assert job.force_sync is False
        assert job.phase == "download"
        assert "Sync deferred (sync slot busy)." in (job.message or "")
    finally:
        db.close()


def test_sync_throttle_delay_seconds_uses_exponential_backoff(monkeypatch):
    request = httpx.Request("GET", "https://api.inaturalist.org/v1/observations?page=2")
    response = httpx.Response(429, request=request, headers={"Retry-After": "120"})
    throttle_error = httpx.HTTPStatusError("normal_throttling", request=request, response=response)

    monkeypatch.setattr(
        export_service,
        "export_config",
        replace(
            export_service.export_config,
            sync_backoff_jitter_ratio=0.0,
            sync_backoff_max_seconds=5000,
        ),
    )

    delay_1, attempt_1 = export_service._sync_throttle_delay_seconds(throttle_error)
    delay_2, attempt_2 = export_service._sync_throttle_delay_seconds(
        throttle_error,
        previous_message="Sync paused by iNaturalist throttling (HTTP 429). backoff_attempt=1.",
    )

    assert attempt_1 == 1
    assert delay_1 == 120
    assert attempt_2 == 2
    assert delay_2 == 240


def test_phase_plan_defers_429_when_cache_exists(monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        _mk_list(db, 15, product_type="project", is_public=True)
        _mk_observation(db, 15, 15001, with_photo=True)
        job = models.ExportJob(
            list_id=15,
            status="queued",
            phase="plan",
            force_sync=True,
            updated_at=now,
            created_at=now,
            next_run_at=now - timedelta(seconds=1),
            message="previous",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        request = httpx.Request("GET", "https://api.inaturalist.org/v1/observations?page=2")
        response = httpx.Response(429, request=request, headers={"Retry-After": "1200"})
        throttle_error = httpx.HTTPStatusError("normal_throttling", request=request, response=response)

        monkeypatch.setattr(
            export_service,
            "sync_list_observations",
            lambda _db, _list: (_ for _ in ()).throw(throttle_error),
        )
        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(
                export_service.export_config,
                sync_backoff_jitter_ratio=0.0,
                sync_backoff_max_seconds=7200,
                sync_max_concurrent=1,
                sync_defer_to_cache_products_csv="project",
            ),
        )

        progressed = export_service._phase_plan(db, job)
        assert progressed is True
        progressed = export_service._phase_plan(db, job)
        assert progressed is True
        assert job.force_sync is False
        assert job.phase == "download"
        assert "Sync deferred (iNaturalist throttling HTTP 429 backoff_attempt=1)." in (job.message or "")
    finally:
        db.close()


def test_schedule_next_run_preserves_waiting_quota_retry_time():
    now = datetime.now(UTC).replace(tzinfo=None)
    retry_at = now + timedelta(minutes=45)
    job = models.ExportJob(
        list_id=99,
        status="waiting_quota",
        phase="plan",
        size_bucket="S",
        next_run_at=retry_at,
    )

    export_service._schedule_next_run(job, now)
    assert job.status == "waiting_quota"
    assert job.next_run_at == retry_at
    assert job.last_run_at == now


def test_process_pending_publish_jobs_publishes_ready_job(tmp_path, monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        export_root = tmp_path / "exports"
        publish_root = tmp_path / "published"
        cfg = replace(
            export_service.export_config,
            publish_enabled=True,
            publish_backend="filesystem",
            publish_dir=str(publish_root),
            publish_base_url="https://downloads.example.org/mydnaobv",
            storage_dir=str(export_root),
            publish_jobs_per_run=1,
        )
        monkeypatch.setattr(export_service, "export_config", cfg)
        monkeypatch.setattr(publish_module, "export_config", cfg)

        _mk_list(db, 20, product_type="project", is_public=True)
        job = models.ExportJob(
            id=200,
            list_id=20,
            status="ready",
            phase="done",
            created_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
            message="Export complete.",
        )
        db.add(job)
        db.flush()

        final_dir = export_root / "job_200" / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        zip_path = final_dir / "example.zip"
        zip_path.write_bytes(b"zip-bytes")

        db.add(
            models.ExportArtifact(
                job_id=200,
                kind="zip",
                part_number=None,
                relative_path="job_200/final/example.zip",
                size_bytes=zip_path.stat().st_size,
            )
        )
        db.commit()

        published = export_service.process_pending_publish_jobs(db, limit=1)
        db.refresh(job)

        assert published == 1
        assert "Publish complete." in (job.message or "")
        assert (publish_root / "list_20" / "latest" / "example.zip").exists()
        assert publish_module.is_latest_job_published(20, 200) is True
    finally:
        db.close()


def test_split_large_zip_creates_ordered_chunks(tmp_path):
    zip_path = tmp_path / "sample.zip"
    zip_path.write_bytes(b"x" * (3 * 1024 * 1024 + 17))

    chunks = export_service._split_large_zip(zip_path, chunk_size_mb=1)
    assert len(chunks) == 4
    assert chunks[0].name.endswith(".part001")
    assert chunks[-1].name.endswith(".part004")
    assert sum(chunk.stat().st_size for chunk in chunks) == zip_path.stat().st_size


def test_cleanup_expired_exports_rolls_back_before_file_cleanup(tmp_path, monkeypatch):
    db = _session()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        obs_list = _mk_list(db, 30, product_type="project", is_public=True)
        job = models.ExportJob(
            id=3000,
            list_id=obs_list.id,
            status="ready",
            phase="done",
            created_at=now - timedelta(hours=5),
            finished_at=now - timedelta(hours=4),
            updated_at=now - timedelta(hours=4),
        )
        db.add(job)
        db.commit()

        job_dir = tmp_path / "job_3000"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "dummy.txt").write_text("x", encoding="utf-8")

        rollback_calls = {"count": 0}
        original_rollback = db.rollback

        def counting_rollback():
            rollback_calls["count"] += 1
            return original_rollback()

        monkeypatch.setattr(db, "rollback", counting_rollback)

        def fail_commit():
            raise AssertionError("cleanup_expired_exports should not call commit")

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(
            export_service,
            "export_config",
            replace(export_service.export_config, storage_dir=str(tmp_path), retention_hours=1),
        )

        removed = export_service.cleanup_expired_exports(db)
        assert removed == 1
        assert rollback_calls["count"] >= 1
        assert not job_dir.exists()
    finally:
        db.close()
