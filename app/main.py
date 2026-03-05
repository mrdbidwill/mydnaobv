from datetime import UTC, datetime, timedelta
from typing import Optional
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, Depends, Query, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
import secrets

from app.core.config import settings
from app.db import get_db
from app import models
from app.exports.service import (
    artifact_abspath,
    enqueue_export_job_for_list,
    get_artifact_for_job,
    list_artifacts_for_job,
    list_jobs_for_list,
    latest_completed_job_for_list,
)
from app.exports.publish import latest_artifact_exists, published_job_url, published_latest_url
from app.services.inat import fetch_observations_for_list


templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name)
security = HTTPBasic()


PAGE_SIZE = 10
OBS_PAGE_SIZE = 15
EXPORT_PAGE_SIZE = 12
DOWNLOAD_PAGE_SIZE = 20


def utc_now_naive() -> datetime:
    # DB columns are timestamp without timezone; persist UTC clock time as naive.
    return datetime.now(UTC).replace(tzinfo=None)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_user_filters(inat_user_id_raw: str, inat_username_raw: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    user_id_text = (inat_user_id_raw or "").strip()
    username = (inat_username_raw or "").strip() or None
    user_id_int: Optional[int] = None

    if user_id_text:
        try:
            user_id_int = int(user_id_text)
            if user_id_int <= 0:
                raise ValueError
        except ValueError:
            return None, None, "Please provide a valid numeric iNaturalist user ID."

    if username and " " in username:
        return None, None, "iNaturalist username cannot contain spaces."

    if user_id_int is None and not username:
        return None, None, "Provide either an iNaturalist user ID or iNaturalist username."

    return user_id_int, username, None


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


def require_export_access(credentials: HTTPBasicCredentials = Depends(security)):
    try:
        allowed_credentials = settings.export_operator_credentials()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export auth config error: {exc}",
        ) from exc

    matched = False
    for username, password in allowed_credentials:
        username_ok = secrets.compare_digest(credentials.username, username)
        password_ok = secrets.compare_digest(credentials.password, password)
        if username_ok and password_ok:
            matched = True
            break

    if not matched:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


@app.get("/")
def index(request: Request, page: int = Query(default=1, ge=1), db: Session = Depends(get_db)):
    total = db.query(func.count(models.ObservationList.id)).scalar() or 0
    lists = (
        db.query(models.ObservationList)
        .order_by(models.ObservationList.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "lists": lists,
            "page": page,
            "pages": pages,
            "dna_field_id": settings.inat_dna_field_id or "",
            "public_downloads_enabled": settings.export_public_downloads_enabled,
        },
    )


@app.post("/lists/create")
def create_list(
    request: Request,
    title: str = Form(...),
    description: str = Form(default=""),
    inat_user_id: str = Form(default=""),
    inat_username: str = Form(default=""),
    dna_field_id: str = Form(default=""),
    taxon_filter: str = Form(default=""),
    place_query: str = Form(default=""),
    db: Session = Depends(get_db),
):
    title = title.strip()
    if not title:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "error": "Please provide a list title.",
                "lists": [],
                "page": 1,
                "pages": 1,
                "dna_field_id": dna_field_id or settings.inat_dna_field_id or "",
            },
            status_code=400,
        )

    user_id_int, username, user_error = parse_user_filters(inat_user_id, inat_username)
    if user_error:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "error": user_error,
                "lists": [],
                "page": 1,
                "pages": 1,
                "dna_field_id": dna_field_id or settings.inat_dna_field_id or "",
            },
            status_code=400,
        )

    obs_list = models.ObservationList(
        title=title,
        description=description.strip() or None,
        inat_user_id=user_id_int,
        inat_username=username,
        inat_place_id=None,
        place_query=place_query.strip() or None,
        inat_dna_field_id=dna_field_id.strip() or settings.inat_dna_field_id,
        taxon_filter=taxon_filter.strip() or None,
    )
    db.add(obs_list)
    db.commit()
    db.refresh(obs_list)

    return RedirectResponse(url=f"/lists/{obs_list.id}", status_code=303)


@app.get("/lists/{list_id}")
def list_page(
    request: Request,
    list_id: int,
    refresh: bool = False,
    export_notice: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": None,
                "observations": [],
                "error": "List not found.",
            },
            status_code=404,
        )

    ttl = timedelta(hours=settings.cache_ttl_hours)
    last_sync_at_utc = as_utc(obs_list.last_sync_at)
    needs_sync = refresh or not last_sync_at_utc or (datetime.now(UTC) - last_sync_at_utc) > ttl

    sync_error = None
    if needs_sync and obs_list.inat_dna_field_id:
        try:
            observations = fetch_observations_for_list(obs_list)
            for obs in observations:
                existing = (
                    db.query(models.Observation)
                    .filter_by(list_id=obs_list.id, inat_observation_id=obs.inat_id)
                    .first()
                )
                if existing:
                    existing.taxon_name = obs.taxon_name
                    existing.species_guess = obs.species_guess
                    existing.scientific_name = obs.scientific_name
                    existing.common_name = obs.common_name
                    existing.user_name = obs.user_name
                    existing.observed_at = obs.observed_at
                    existing.inat_url = obs.inat_url
                    existing.dna_field_value = obs.dna_field_value
                    existing.photo_url = obs.photo_url
                    existing.photo_license_code = obs.photo_license_code
                    existing.photo_attribution = obs.photo_attribution
                    record = existing
                else:
                    record = models.Observation(
                        inat_observation_id=obs.inat_id,
                        taxon_name=obs.taxon_name,
                        species_guess=obs.species_guess,
                        scientific_name=obs.scientific_name,
                        common_name=obs.common_name,
                        user_name=obs.user_name,
                        observed_at=obs.observed_at,
                        inat_url=obs.inat_url,
                        dna_field_value=obs.dna_field_value,
                        photo_url=obs.photo_url,
                        photo_license_code=obs.photo_license_code,
                        photo_attribution=obs.photo_attribution,
                        list_id=obs_list.id,
                    )
                    db.add(record)
                    db.flush()

                db.query(models.ObservationPhoto).filter_by(observation_id=record.id).delete(
                    synchronize_session=False
                )
                for photo in obs.photo_entries:
                    db.add(
                        models.ObservationPhoto(
                            observation_id=record.id,
                            inat_photo_id=photo.inat_photo_id,
                            photo_index=photo.photo_index,
                            photo_url=photo.photo_url,
                            photo_license_code=photo.photo_license_code,
                            photo_attribution=photo.photo_attribution,
                        )
                    )
            obs_list.last_sync_at = utc_now_naive()
            db.commit()
        except Exception as exc:
            db.rollback()
            sync_error = f"Sync failed: {exc}"

    total_obs = (
        db.query(func.count(models.Observation.id))
        .filter_by(list_id=obs_list.id)
        .scalar()
        or 0
    )
    obs_pages = max(1, (total_obs + OBS_PAGE_SIZE - 1) // OBS_PAGE_SIZE)
    observations = (
        db.query(models.Observation)
        .filter_by(list_id=obs_list.id)
        .order_by(models.Observation.observed_at.desc().nullslast())
        .offset((page - 1) * OBS_PAGE_SIZE)
        .limit(OBS_PAGE_SIZE)
        .all()
    )

    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "list": obs_list,
            "observations": observations,
            "cache_ttl_hours": settings.cache_ttl_hours,
            "sync_error": sync_error,
            "page": page,
            "pages": obs_pages,
            "max_observations": settings.max_observations,
            "pdf_exports_enabled": settings.enable_pdf_exports,
            "public_downloads_enabled": settings.export_public_downloads_enabled,
            "export_notice": export_notice,
        },
    )


@app.get("/exports")
def exports_center(
    request: Request,
    notice: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    _: bool = Depends(require_export_access),
):
    if not settings.enable_pdf_exports:
        return templates.TemplateResponse(
            "exports.html",
            {
                "request": request,
                "lists": [],
                "jobs_by_list": {},
                "artifacts_by_job": {},
                "page": 1,
                "pages": 1,
                "error": "PDF exports are currently disabled by configuration.",
                "export_include_all_photos": settings.export_include_all_photos,
                "export_max_photos_per_observation": settings.export_max_photos_per_observation,
                "publish_enabled": settings.export_publish_enabled,
                "published_job_urls": {},
                "published_latest_urls": {},
                "notice": notice,
            },
            status_code=503,
        )

    total = db.query(func.count(models.ObservationList.id)).scalar() or 0
    lists = (
        db.query(models.ObservationList)
        .order_by(models.ObservationList.created_at.desc())
        .offset((page - 1) * EXPORT_PAGE_SIZE)
        .limit(EXPORT_PAGE_SIZE)
        .all()
    )
    pages = max(1, (total + EXPORT_PAGE_SIZE - 1) // EXPORT_PAGE_SIZE)

    jobs_by_list: dict[int, list[models.ExportJob]] = {}
    artifacts_by_job: dict[int, list[models.ExportArtifact]] = {}
    published_job_urls: dict[int, str] = {}
    published_latest_urls: dict[int, str] = {}
    for obs_list in lists:
        jobs = list_jobs_for_list(db, obs_list.id, limit=6)
        jobs_by_list[obs_list.id] = jobs
        for job in jobs:
            artifacts_by_job[job.id] = list_artifacts_for_job(db, job.id)
            for artifact in artifacts_by_job[job.id]:
                if latest_artifact_exists(obs_list.id, artifact):
                    latest_url = published_latest_url(obs_list.id, artifact)
                    if latest_url:
                        published_latest_urls[artifact.id] = latest_url
                job_url = published_job_url(obs_list.id, job.id, artifact)
                if job_url:
                    published_job_urls[artifact.id] = job_url

    return templates.TemplateResponse(
        "exports.html",
        {
            "request": request,
            "lists": lists,
            "jobs_by_list": jobs_by_list,
            "artifacts_by_job": artifacts_by_job,
            "page": page,
            "pages": pages,
            "error": None,
            "export_include_all_photos": settings.export_include_all_photos,
            "export_max_photos_per_observation": settings.export_max_photos_per_observation,
            "publish_enabled": settings.export_publish_enabled,
            "published_job_urls": published_job_urls,
            "published_latest_urls": published_latest_urls,
            "notice": notice,
        },
    )


@app.get("/downloads")
def public_downloads(
    request: Request,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    if not settings.export_public_downloads_enabled:
        raise HTTPException(status_code=404, detail="Not found")

    total = db.query(func.count(models.ObservationList.id)).scalar() or 0
    lists = (
        db.query(models.ObservationList)
        .order_by(models.ObservationList.created_at.desc())
        .offset((page - 1) * DOWNLOAD_PAGE_SIZE)
        .limit(DOWNLOAD_PAGE_SIZE)
        .all()
    )
    pages = max(1, (total + DOWNLOAD_PAGE_SIZE - 1) // DOWNLOAD_PAGE_SIZE)

    latest_job_by_list: dict[int, models.ExportJob] = {}
    artifacts_by_list: dict[int, list[models.ExportArtifact]] = {}
    published_latest_urls: dict[int, str] = {}

    for obs_list in lists:
        latest_job = latest_completed_job_for_list(db, obs_list.id)
        if not latest_job:
            continue
        latest_job_by_list[obs_list.id] = latest_job
        artifacts = list_artifacts_for_job(db, latest_job.id)
        artifacts_by_list[obs_list.id] = artifacts
        for artifact in artifacts:
            if latest_artifact_exists(obs_list.id, artifact):
                latest_url = published_latest_url(obs_list.id, artifact)
                if latest_url:
                    published_latest_urls[artifact.id] = latest_url

    return templates.TemplateResponse(
        "downloads.html",
        {
            "request": request,
            "lists": lists,
            "latest_job_by_list": latest_job_by_list,
            "artifacts_by_list": artifacts_by_list,
            "published_latest_urls": published_latest_urls,
            "page": page,
            "pages": pages,
        },
    )


@app.post("/exports/lists/{list_id}/queue")
def exports_queue_list(
    list_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_export_access),
):
    if not settings.enable_pdf_exports:
        return RedirectResponse(url="/exports", status_code=303)

    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return RedirectResponse(url="/exports", status_code=303)

    _, _, message = enqueue_export_job_for_list(
        db,
        obs_list=obs_list,
        requested_by="exports-center",
        only_if_stale=True,
    )
    return RedirectResponse(url=f"/exports?notice={quote(message)}", status_code=303)


@app.post("/lists/{list_id}/exports/create")
def create_list_export(
    list_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_export_access),
):
    if not settings.enable_pdf_exports:
        return RedirectResponse(url=f"/lists/{list_id}", status_code=303)

    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return RedirectResponse(url="/", status_code=303)

    _, _, message = enqueue_export_job_for_list(
        db,
        obs_list=obs_list,
        requested_by="web",
        only_if_stale=True,
    )
    return RedirectResponse(url=f"/lists/{list_id}?export_notice={quote(message)}", status_code=303)


@app.get("/lists/{list_id}/exports/{job_id}/artifacts/{artifact_id}/download")
def download_export_artifact(
    list_id: int,
    job_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_export_access),
):
    if not settings.enable_pdf_exports:
        raise HTTPException(status_code=404, detail="Not found")

    job = db.query(models.ExportJob).filter_by(id=job_id, list_id=list_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    artifact = get_artifact_for_job(db, job_id=job.id, artifact_id=artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    artifact_path = artifact_abspath(artifact)
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="File not available")

    return FileResponse(path=str(artifact_path), filename=artifact_path.name)


@app.get("/exports/jobs/{job_id}/artifacts/{artifact_id}/download")
def download_export_artifact_by_job(
    job_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_export_access),
):
    if not settings.enable_pdf_exports:
        raise HTTPException(status_code=404, detail="Not found")

    job = db.query(models.ExportJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    artifact = get_artifact_for_job(db, job_id=job.id, artifact_id=artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    artifact_path = artifact_abspath(artifact)
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="File not available")

    return FileResponse(path=str(artifact_path), filename=artifact_path.name)


@app.get("/admin")
def admin_page(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    lists = (
        db.query(models.ObservationList)
        .order_by(models.ObservationList.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "lists": lists, "max_observations": settings.max_observations},
    )


@app.post("/admin/lists/{list_id}/delete")
def admin_delete_list(
    list_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    job_ids = [row[0] for row in db.query(models.ExportJob.id).filter_by(list_id=list_id).all()]
    if job_ids:
        db.query(models.ExportArtifact).filter(models.ExportArtifact.job_id.in_(job_ids)).delete(
            synchronize_session=False
        )
        db.query(models.ExportItem).filter(models.ExportItem.job_id.in_(job_ids)).delete(
            synchronize_session=False
        )
        db.query(models.ExportJob).filter(models.ExportJob.id.in_(job_ids)).delete(
            synchronize_session=False
        )
    observation_ids = [row[0] for row in db.query(models.Observation.id).filter_by(list_id=list_id).all()]
    if observation_ids:
        db.query(models.ObservationPhoto).filter(
            models.ObservationPhoto.observation_id.in_(observation_ids)
        ).delete(synchronize_session=False)
    db.query(models.Observation).filter_by(list_id=list_id).delete(synchronize_session=False)
    db.query(models.ObservationList).filter_by(id=list_id).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/lists/{list_id}/sync")
def admin_sync_list(
    list_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return RedirectResponse(url="/admin", status_code=303)
    obs_list.last_sync_at = None
    db.commit()
    return RedirectResponse(url=f"/lists/{list_id}?refresh=true", status_code=303)


@app.post("/lists/{list_id}/edit")
def edit_list(
    request: Request,
    list_id: int,
    title: str = Form(...),
    description: str = Form(default=""),
    inat_user_id: str = Form(default=""),
    inat_username: str = Form(default=""),
    dna_field_id: str = Form(default=""),
    taxon_filter: str = Form(default=""),
    place_query: str = Form(default=""),
    db: Session = Depends(get_db),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": None,
                "observations": [],
                "error": "List not found.",
            },
            status_code=404,
        )

    title = title.strip()
    if not title:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": obs_list,
                "observations": [],
                "error": "Title is required.",
            },
            status_code=400,
        )

    user_id_int, username, user_error = parse_user_filters(inat_user_id, inat_username)
    if user_error:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": obs_list,
                "observations": [],
                "error": user_error,
            },
            status_code=400,
        )

    obs_list.title = title
    obs_list.description = description.strip() or None
    obs_list.inat_user_id = user_id_int
    obs_list.inat_username = username
    new_place_query = place_query.strip() or None
    if obs_list.place_query != new_place_query:
        obs_list.inat_place_id = None
    obs_list.place_query = new_place_query
    obs_list.inat_dna_field_id = dna_field_id.strip() or settings.inat_dna_field_id
    obs_list.taxon_filter = taxon_filter.strip() or None
    db.commit()

    return RedirectResponse(url=f"/lists/{obs_list.id}", status_code=303)
