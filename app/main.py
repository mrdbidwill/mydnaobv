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
from app.exports.estimate import estimate_list_export_eta, estimate_precheck_from_observations
from app.services.inat import fetch_observations_for_list
from app.services.inat import estimate_total_observations
from app.services.inat import resolve_project_filter
from app.services.us_counties import STATE_OPTIONS, fetch_counties_for_state, normalize_state_code


templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name)
security = HTTPBasic()


PAGE_SIZE = 10
OBS_PAGE_SIZE = 15
EXPORT_PAGE_SIZE = 12
DOWNLOAD_PAGE_SIZE = 20
ADMIN_PAGE_SIZE = 25


def load_index_lists(db: Session, page: int) -> tuple[list[models.ObservationList], int]:
    total = db.query(func.count(models.ObservationList.id)).scalar() or 0
    lists = (
        db.query(models.ObservationList)
        .order_by(models.ObservationList.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return lists, pages


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


def parse_optional_user_filters(
    inat_user_id_raw: str,
    inat_username_raw: str,
) -> tuple[Optional[int], Optional[str], Optional[str]]:
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

    return user_id_int, username, None


def parse_project_filter(inat_project_id_raw: str) -> tuple[Optional[str], Optional[str]]:
    project_id = (inat_project_id_raw or "").strip()
    if not project_id:
        return None, None
    if " " in project_id:
        return None, "iNaturalist project ID/slug cannot contain spaces."
    return project_id, None


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
    lists, pages = load_index_lists(db, page)

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
            "form_title": "",
            "form_description": "",
            "form_inat_user_id": "",
            "form_inat_username": "",
            "form_place_query": "",
            "form_dna_field_id": settings.inat_dna_field_id or "",
            "form_taxon_filter": "",
            "precheck_notice": None,
            "limits_explanation": "These limits are in place to keep exports dependable for everyone, protect shared VPS resources, and respect iNaturalist API/media capacity.",
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
    action: str = Form(default="save"),
    db: Session = Depends(get_db),
):
    page = 1
    lists, pages = load_index_lists(db, page)
    title = title.strip()
    description_clean = description.strip()
    inat_user_id_raw = (inat_user_id or "").strip()
    inat_username_raw = (inat_username or "").strip()
    place_query_clean = (place_query or "").strip()
    dna_field_clean = dna_field_id.strip() or settings.inat_dna_field_id or ""
    taxon_filter_clean = (taxon_filter or "").strip()

    form_context = {
        "form_title": title,
        "form_description": description_clean,
        "form_inat_user_id": inat_user_id_raw,
        "form_inat_username": inat_username_raw,
        "form_place_query": place_query_clean,
        "form_dna_field_id": dna_field_clean,
        "form_taxon_filter": taxon_filter_clean,
        "precheck_notice": None,
        "limits_explanation": "These limits are in place to keep exports dependable for everyone, protect shared VPS resources, and respect iNaturalist API/media capacity.",
    }

    if not title:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "error": "Please provide a list title.",
                "lists": lists,
                "page": page,
                "pages": pages,
                "dna_field_id": dna_field_clean,
                "public_downloads_enabled": settings.export_public_downloads_enabled,
                **form_context,
            },
            status_code=400,
        )

    user_id_int, username, user_error = parse_user_filters(inat_user_id_raw, inat_username_raw)
    if user_error:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "error": user_error,
                "lists": lists,
                "page": page,
                "pages": pages,
                "dna_field_id": dna_field_clean,
                "public_downloads_enabled": settings.export_public_downloads_enabled,
                **form_context,
            },
            status_code=400,
        )

    if action == "estimate":
        precheck_notice = None
        try:
            precheck = estimate_total_observations(
                inat_user_id=user_id_int,
                inat_username=username,
                place_query=place_query_clean or None,
                taxon_filter=taxon_filter_clean or None,
            )
            precheck_eta = estimate_precheck_from_observations(precheck["total_results"])
            place_match_text = ""
            if precheck.get("resolved_place_name"):
                place_match_text = (
                    f"Matched iNaturalist location: {precheck['resolved_place_name']} "
                    f"(place_id {precheck['resolved_place_id']}). "
                )
            precheck_notice = (
                place_match_text
                + 
                f"Pre-check estimate: about {precheck['total_results']} matching observations. "
                f"Rough export size around {precheck_eta['eligible_items']} pages; likely completion {precheck_eta['eta_likely']} "
                f"(best {precheck_eta['eta_best']}, worst {precheck_eta['eta_worst']}). "
                "For faster turnaround, narrow by genus and/or a smaller place."
            )
        except Exception as exc:
            precheck_notice = f"Pre-check estimate unavailable: {exc}"

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "lists": lists,
                "page": page,
                "pages": pages,
                "dna_field_id": dna_field_clean,
                "public_downloads_enabled": settings.export_public_downloads_enabled,
                **form_context,
                "precheck_notice": precheck_notice,
            },
            status_code=200,
        )

    obs_list = models.ObservationList(
        title=title,
        description=description_clean or None,
        inat_user_id=user_id_int,
        inat_username=username,
        inat_place_id=None,
        place_query=place_query_clean or None,
        inat_dna_field_id=dna_field_clean or settings.inat_dna_field_id,
        taxon_filter=taxon_filter_clean or None,
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
    export_eta = estimate_list_export_eta(db, obs_list.id) if settings.enable_pdf_exports else None
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
            "export_eta": export_eta,
            "limits_explanation": "These limits are in place to keep exports dependable for everyone, protect shared VPS resources, and respect iNaturalist API/media capacity.",
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
                "eta_by_list": {},
                "limits_explanation": "These limits are in place to keep exports dependable for everyone, protect shared VPS resources, and respect iNaturalist API/media capacity.",
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
    eta_by_list: dict[int, dict[str, object]] = {}
    for obs_list in lists:
        jobs = list_jobs_for_list(db, obs_list.id, limit=6)
        jobs_by_list[obs_list.id] = jobs
        eta_by_list[obs_list.id] = estimate_list_export_eta(db, obs_list.id)
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
            "eta_by_list": eta_by_list,
            "limits_explanation": "These limits are in place to keep exports dependable for everyone, protect shared VPS resources, and respect iNaturalist API/media capacity.",
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
    notice: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    total = db.query(func.count(models.ObservationList.id)).scalar() or 0
    pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    current_page = min(page, pages)

    lists = (
        db.query(models.ObservationList)
        .order_by(models.ObservationList.created_at.desc())
        .offset((current_page - 1) * ADMIN_PAGE_SIZE)
        .limit(ADMIN_PAGE_SIZE)
        .all()
    )
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "lists": lists,
            "page": current_page,
            "pages": pages,
            "max_observations": settings.max_observations,
            "notice": notice,
            "error": error,
            "state_options": STATE_OPTIONS,
            "default_state_code": "AL",
            "default_project_id": settings.inat_default_project_id or "",
        },
    )


@app.post("/admin/projects/county-seed")
def admin_seed_project_counties(
    inat_project_id: str = Form(...),
    state_code: str = Form(...),
    description_prefix: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    project_id, project_error = parse_project_filter(inat_project_id)
    if project_error:
        return RedirectResponse(url=f"/admin?error={quote(project_error)}", status_code=303)
    if not project_id:
        return RedirectResponse(
            url="/admin?error=Please+provide+an+iNaturalist+project+ID+or+slug.",
            status_code=303,
        )
    try:
        canonical_project_id, _, project_title = resolve_project_filter(project_id)
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin?error={quote(str(exc))}",
            status_code=303,
        )

    normalized_state = normalize_state_code(state_code)
    if not normalized_state:
        return RedirectResponse(url="/admin?error=Please+select+a+valid+US+state.", status_code=303)

    try:
        county_rows = fetch_counties_for_state(normalized_state)
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin?error={quote(f'Unable to load county list: {exc}')}",
            status_code=303,
        )

    created = 0
    skipped_existing = 0
    description_prefix_clean = (description_prefix or "").strip()

    for row in county_rows:
        existing = (
            db.query(models.ObservationList)
            .filter(
                models.ObservationList.inat_project_id == canonical_project_id,
                models.ObservationList.place_query == row.place_query,
            )
            .first()
        )
        if existing:
            skipped_existing += 1
            continue

        title = f"{row.county_name}-{normalized_state} — {canonical_project_id}"
        description_parts = [
            "Auto-generated county list.",
            f"Project: {canonical_project_id}.",
            f"Place: {row.place_query}.",
        ]
        if project_title:
            description_parts.append(f"Project title: {project_title}.")
        if description_prefix_clean:
            description_parts.insert(0, description_prefix_clean)

        obs_list = models.ObservationList(
            title=title,
            description=" ".join(description_parts),
            inat_user_id=None,
            inat_username=None,
            inat_project_id=canonical_project_id,
            inat_place_id=None,
            place_query=row.place_query,
            inat_dna_field_id=settings.inat_dna_field_id,
            taxon_filter=None,
        )
        db.add(obs_list)
        created += 1

    db.commit()
    notice = (
        f"County seeding complete for {normalized_state} and project {canonical_project_id}: "
        f"created {created}, skipped existing {skipped_existing}, total counties {len(county_rows)}."
    )
    return RedirectResponse(url=f"/admin?notice={quote(notice)}", status_code=303)


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
    inat_project_id: str = Form(default=""),
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

    user_id_int, username, user_error = parse_optional_user_filters(inat_user_id, inat_username)
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

    project_id, project_error = parse_project_filter(inat_project_id)
    if project_error:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": obs_list,
                "observations": [],
                "error": project_error,
            },
            status_code=400,
        )

    if user_id_int is None and not username and not project_id:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": obs_list,
                "observations": [],
                "error": "Provide an iNaturalist user ID, username, or project ID/slug.",
            },
            status_code=400,
        )

    obs_list.title = title
    obs_list.description = description.strip() or None
    obs_list.inat_user_id = user_id_int
    obs_list.inat_username = username
    obs_list.inat_project_id = project_id
    new_place_query = place_query.strip() or None
    if obs_list.place_query != new_place_query:
        obs_list.inat_place_id = None
    obs_list.place_query = new_place_query
    obs_list.inat_dna_field_id = dna_field_id.strip() or settings.inat_dna_field_id
    obs_list.taxon_filter = taxon_filter.strip() or None
    db.commit()

    return RedirectResponse(url=f"/lists/{obs_list.id}", status_code=303)
