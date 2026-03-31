from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
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

        progressed = export_service._phase_plan(db, job)
        assert progressed is False
        assert job.status == "waiting_quota"
        assert job.phase == "plan"
        assert "HTTP 429" in (job.message or "")
        assert job.next_run_at is not None
        assert job.next_run_at > now
        assert job.finished_at is None
        assert job.force_sync is True
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
