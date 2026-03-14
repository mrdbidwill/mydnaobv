from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
from typing import Optional
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, Depends, Query, HTTPException, status
from fastapi.staticfiles import StaticFiles
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
from app.exports.publish import latest_artifact_exists, published_latest_url
from app.exports.estimate import estimate_list_export_eta, estimate_precheck_from_observations
from app.services.inat import fetch_observations_for_list
from app.services.inat import estimate_total_observations
from app.services.inat import resolve_project_filter
from app.services.list_sync import sync_list_observations
from app.services.us_counties import STATE_OPTIONS, fetch_counties_for_state, normalize_state_code


templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
security = HTTPBasic()


PAGE_SIZE = 10
OBS_PAGE_SIZE = 15
ADMIN_PAGE_SIZE = 25
PUBLIC_COUNTY_PAGE_SIZE = 24
PUBLIC_REFRESH_INTERVAL_DAYS = max(1, settings.public_refresh_interval_days)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> RedirectResponse:
    return RedirectResponse(url="/static/images/mrdbid_logo.svg", status_code=307)


def template_response(
    request: Request,
    template_name: str,
    context: dict[str, object],
    *,
    show_ads: bool = False,
    status_code: int = 200,
):
    adsense_client_id = (settings.adsense_client_id or "").strip()
    adsense_banner_slot = (settings.adsense_banner_slot or "").strip()
    render_ads = bool(show_ads and settings.adsense_enabled and adsense_client_id)
    payload = {
        "request": request,
        "show_adsense": render_ads,
        "adsense_client_id": adsense_client_id,
        "adsense_banner_slot": adsense_banner_slot,
    }
    payload.update(context)
    return templates.TemplateResponse(template_name, payload, status_code=status_code)


def normalize_index_sort(sort: str | None) -> str:
    candidate = (sort or "").strip().lower()
    if candidate in ("title_asc", "created_desc"):
        return candidate
    return "title_asc"


def _preferred_county_file_artifact(artifacts: list[models.ExportArtifact]) -> models.ExportArtifact | None:
    for wanted in ("merged_pdf", "zip"):
        for artifact in artifacts:
            if artifact.kind == wanted:
                return artifact
    return None


def _artifact_by_kind(artifacts: list[models.ExportArtifact], kind: str) -> models.ExportArtifact | None:
    for artifact in artifacts:
        if artifact.kind == kind:
            return artifact
    return None


def _artifact_public_url(list_id: int, artifact: models.ExportArtifact | None) -> str | None:
    if not artifact:
        return None
    if latest_artifact_exists(list_id, artifact):
        latest_url = published_latest_url(list_id, artifact)
        if latest_url:
            return latest_url
    return f"/public/lists/{list_id}/artifacts/{artifact.id}/download"


def _cleanup_list_export_files(job_ids: list[int], list_id: int) -> None:
    storage_root = Path(settings.export_storage_dir)
    for job_id in job_ids:
        shutil.rmtree(storage_root / f"job_{job_id}", ignore_errors=True)

    publish_root = (settings.export_publish_dir or "").strip()
    if publish_root:
        shutil.rmtree(Path(publish_root) / f"list_{list_id}", ignore_errors=True)


def _format_utc_date(value: datetime | None) -> str:
    utc_value = as_utc(value)
    if not utc_value:
        return "Not refreshed yet"
    return utc_value.strftime("%Y-%m-%d")


def _refresh_summary(last_sync_at: datetime | None) -> dict[str, object]:
    if not last_sync_at:
        return {
            "last_refreshed_label": "Not refreshed yet",
            "next_refresh_label": "Refresh pending",
            "is_due": True,
        }

    last_sync_utc = as_utc(last_sync_at)
    now_utc = as_utc(utc_now_naive())
    if not last_sync_utc or not now_utc:
        return {
            "last_refreshed_label": "Not refreshed yet",
            "next_refresh_label": "Refresh pending",
            "is_due": True,
        }

    next_due = last_sync_utc + timedelta(days=PUBLIC_REFRESH_INTERVAL_DAYS)
    return {
        "last_refreshed_label": _format_utc_date(last_sync_utc),
        "next_refresh_label": _format_utc_date(next_due),
        "is_due": now_utc >= next_due,
    }


def _state_label_map() -> dict[str, str]:
    return {code: label for code, label in STATE_OPTIONS}


def _configured_public_states() -> set[str]:
    raw = (settings.public_state_codes or "").strip()
    if not raw:
        return set()

    tokens = [token.strip() for token in raw.split(",") if token.strip()]
    if any(token.upper() in ("ALL", "*") for token in tokens):
        return {code for code, _ in STATE_OPTIONS}

    out: set[str] = set()
    for token in tokens:
        normalized = normalize_state_code(token)
        if normalized:
            out.add(normalized)
    return out


def load_public_county_rows(
    db: Session,
    page: int,
    state_code: str | None,
) -> tuple[list[dict[str, object]], int, int, list[tuple[str, str]], str]:
    normalized_state = normalize_state_code(state_code or "") or ""
    allowed_states = _configured_public_states()
    if allowed_states and normalized_state and normalized_state not in allowed_states:
        normalized_state = ""

    base_query = db.query(models.ObservationList).filter(
        models.ObservationList.product_type == "county",
        models.ObservationList.is_public_download.is_(True),
    )
    if allowed_states:
        base_query = base_query.filter(models.ObservationList.state_code.in_(sorted(allowed_states)))
    if normalized_state:
        base_query = base_query.filter(models.ObservationList.state_code == normalized_state)

    total = base_query.count()
    pages = max(1, (total + PUBLIC_COUNTY_PAGE_SIZE - 1) // PUBLIC_COUNTY_PAGE_SIZE)
    current_page = min(page, pages)
    rows = (
        base_query
        .order_by(
            models.ObservationList.state_code.asc().nullslast(),
            models.ObservationList.county_name.asc().nullslast(),
            models.ObservationList.title.asc(),
            models.ObservationList.id.asc(),
        )
        .offset((current_page - 1) * PUBLIC_COUNTY_PAGE_SIZE)
        .limit(PUBLIC_COUNTY_PAGE_SIZE)
        .all()
    )

    state_rows = (
        db.query(models.ObservationList.state_code)
        .filter(
            models.ObservationList.product_type == "county",
            models.ObservationList.is_public_download.is_(True),
            models.ObservationList.state_code.isnot(None),
        )
        .join(models.ExportJob, models.ExportJob.list_id == models.ObservationList.id)
        .filter(models.ExportJob.status.in_(("ready", "partial_ready")))
        .distinct()
        .order_by(models.ObservationList.state_code.asc())
        .all()
    )
    labels = _state_label_map()
    state_options = [
        (code, labels.get(code, code))
        for (code,) in state_rows
        if code and (not allowed_states or code in allowed_states)
    ]

    catalog: list[dict[str, object]] = []
    for obs_list in rows:
        latest_job = latest_completed_job_for_list(db, obs_list.id)
        county_artifact = None
        index_artifact = None
        county_download_url = None
        county_download_label = None
        index_download_url = None
        status_label = "Not built"
        if latest_job:
            artifacts = list_artifacts_for_job(db, latest_job.id)
            county_artifact = _preferred_county_file_artifact(artifacts)
            index_artifact = _artifact_by_kind(artifacts, "observations_index_pdf")
            status_label = latest_job.status
            if county_artifact and index_artifact:
                status_label = "Ready"
            elif county_artifact:
                status_label = "County file ready"
            elif index_artifact:
                status_label = "Observation index ready"

            county_download_url = _artifact_public_url(obs_list.id, county_artifact)
            if county_artifact:
                county_download_label = "County PDF" if county_artifact.kind == "merged_pdf" else "County ZIP"
            index_download_url = _artifact_public_url(obs_list.id, index_artifact)

        refresh_data = _refresh_summary(obs_list.last_sync_at)

        catalog.append(
            {
                "list": obs_list,
                "latest_job": latest_job,
                "county_artifact": county_artifact,
                "index_artifact": index_artifact,
                "status_label": status_label,
                "county_download_url": county_download_url,
                "county_download_label": county_download_label,
                "index_download_url": index_download_url,
                "last_refreshed_label": refresh_data["last_refreshed_label"],
                "next_refresh_label": refresh_data["next_refresh_label"],
                "refresh_due": refresh_data["is_due"],
            }
        )

    return catalog, pages, current_page, state_options, normalized_state


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
def index(
    request: Request,
    page: int = Query(default=1, ge=1),
    state_code: str = Query(default=""),
    db: Session = Depends(get_db),
):
    rows, pages, current_page, state_options, normalized_state = load_public_county_rows(
        db,
        page,
        state_code,
    )

    return template_response(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "rows": rows,
            "page": current_page,
            "pages": pages,
            "state_options": state_options,
            "state_code": normalized_state,
        },
        show_ads=True,
    )


@app.post("/lists/create")
def create_list(
    _: bool = Depends(require_admin),
):
    return RedirectResponse(
        url="/admin?notice=Public+custom+list+creation+is+deprecated.+Use+Admin+county+controls.",
        status_code=303,
    )


@app.get("/lists/{list_id}")
def list_page(
    request: Request,
    list_id: int,
    export_notice: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return template_response(
            request,
            "list.html",
            {
                "list": None,
                "observations": [],
                "error": "List not found.",
            },
            status_code=404,
        )

    sync_error = None

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

    return template_response(
        request,
        "list.html",
        {
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
    _: bool = Depends(require_admin),
):
    return RedirectResponse(
        url="/admin?notice=Export+Center+retired.+Use+Admin+County+Build+Control.",
        status_code=303,
    )


@app.get("/downloads")
def public_downloads(
    _: Request,
):
    return RedirectResponse(url="/", status_code=303)


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


@app.get("/public/lists/{list_id}/artifacts/{artifact_id}/download")
def public_download_latest_artifact(
    list_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        raise HTTPException(status_code=404, detail="List not found")
    if obs_list.product_type != "county" or not obs_list.is_public_download:
        raise HTTPException(status_code=404, detail="Not found")

    latest_job = latest_completed_job_for_list(db, list_id)
    if not latest_job:
        raise HTTPException(status_code=404, detail="No completed export available")

    artifact = get_artifact_for_job(db, job_id=latest_job.id, artifact_id=artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.kind not in ("merged_pdf", "zip", "observations_index_pdf"):
        raise HTTPException(status_code=404, detail="Artifact not public")

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
    state_code: str = Query(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    normalized_state = normalize_state_code(state_code or "") or ""
    base_query = db.query(models.ObservationList).filter(
        models.ObservationList.product_type == "county",
    )
    if normalized_state:
        base_query = base_query.filter(models.ObservationList.state_code == normalized_state)

    total = base_query.count()
    pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    current_page = min(page, pages)

    lists = (
        base_query
        .order_by(
            models.ObservationList.state_code.asc().nullslast(),
            models.ObservationList.county_name.asc().nullslast(),
            models.ObservationList.title.asc(),
        )
        .offset((current_page - 1) * ADMIN_PAGE_SIZE)
        .limit(ADMIN_PAGE_SIZE)
        .all()
    )

    latest_job_by_list: dict[int, models.ExportJob] = {}
    ready_download_url_by_list: dict[int, str] = {}
    for obs_list in lists:
        recent_jobs = list_jobs_for_list(db, obs_list.id, limit=1)
        if recent_jobs:
            latest_job_by_list[obs_list.id] = recent_jobs[0]

        latest_ready = latest_completed_job_for_list(db, obs_list.id)
        if not latest_ready:
            continue
        artifacts = list_artifacts_for_job(db, latest_ready.id)
        chosen = _preferred_county_file_artifact(artifacts)
        if not chosen:
            continue
        if latest_artifact_exists(obs_list.id, chosen):
            latest_url = published_latest_url(obs_list.id, chosen)
            if latest_url:
                ready_download_url_by_list[obs_list.id] = latest_url
                continue
        ready_download_url_by_list[obs_list.id] = (
            f"/public/lists/{obs_list.id}/artifacts/{chosen.id}/download"
        )

    state_rows = (
        db.query(models.ObservationList.state_code)
        .filter(
            models.ObservationList.product_type == "county",
            models.ObservationList.state_code.isnot(None),
        )
        .distinct()
        .order_by(models.ObservationList.state_code.asc())
        .all()
    )
    label_map = _state_label_map()
    present_state_options = [
        (code, label_map.get(code, code))
        for (code,) in state_rows
        if code
    ]

    return template_response(
        request,
        "admin.html",
        {
            "lists": lists,
            "page": current_page,
            "pages": pages,
            "state_code": normalized_state,
            "max_observations": settings.max_observations,
            "notice": notice,
            "error": error,
            "state_options": STATE_OPTIONS,
            "present_state_options": present_state_options,
            "default_state_code": "AL",
            "default_project_id": settings.inat_default_project_id or "",
            "latest_job_by_list": latest_job_by_list,
            "ready_download_url_by_list": ready_download_url_by_list,
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
    queued = 0
    skipped_queue_existing = 0
    description_prefix_clean = (description_prefix or "").strip()

    for row in county_rows:
        existing = (
            db.query(models.ObservationList)
            .filter(
                models.ObservationList.product_type == "county",
                models.ObservationList.state_code == normalized_state,
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
            product_type="county",
            state_code=normalized_state,
            county_name=row.county_name,
            is_public_download=True,
            inat_place_id=None,
            place_query=row.place_query,
            inat_dna_field_id=settings.inat_dna_field_id,
            taxon_filter=None,
        )
        db.add(obs_list)
        created += 1

    db.commit()

    target_lists = (
        db.query(models.ObservationList)
        .filter(
            models.ObservationList.product_type == "county",
            models.ObservationList.state_code == normalized_state,
            models.ObservationList.inat_project_id == canonical_project_id,
        )
        .all()
    )
    for obs_list in target_lists:
        _, was_queued, _ = enqueue_export_job_for_list(
            db,
            obs_list=obs_list,
            requested_by="admin-county-seed",
            only_if_stale=False,
            force_sync=True,
        )
        if was_queued:
            queued += 1
        else:
            skipped_queue_existing += 1

    notice = (
        f"County seeding complete for {normalized_state} and project {canonical_project_id}: "
        f"created {created}, skipped existing {skipped_existing}, total counties {len(county_rows)}. "
        f"Build queue: queued {queued}, already active/up-to-date {skipped_queue_existing}."
    )
    return RedirectResponse(url=f"/admin?notice={quote(notice)}", status_code=303)


@app.post("/admin/states/build")
def admin_build_state_counties(
    state_code: str = Form(...),
    inat_project_id: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    normalized_state = normalize_state_code(state_code or "")
    if not normalized_state:
        return RedirectResponse(url="/admin?error=Please+select+a+valid+US+state.", status_code=303)

    project_id = (inat_project_id or "").strip()
    canonical_project_id: str | None = None
    if project_id:
        try:
            canonical_project_id, _, _ = resolve_project_filter(project_id)
        except Exception as exc:
            return RedirectResponse(url=f"/admin?error={quote(str(exc))}", status_code=303)

    query = db.query(models.ObservationList).filter(
        models.ObservationList.product_type == "county",
        models.ObservationList.state_code == normalized_state,
    )
    if canonical_project_id:
        query = query.filter(models.ObservationList.inat_project_id == canonical_project_id)
    targets = query.all()

    if not targets:
        return RedirectResponse(
            url=f"/admin?error={quote(f'No county products found for state {normalized_state}.')}",
            status_code=303,
        )

    queued = 0
    reused = 0
    for obs_list in targets:
        _, was_queued, _ = enqueue_export_job_for_list(
            db,
            obs_list=obs_list,
            requested_by="admin-build-state",
            only_if_stale=False,
            force_sync=True,
        )
        if was_queued:
            queued += 1
        else:
            reused += 1

    notice = (
        f"Build queued for {normalized_state}: targeted {len(targets)}, "
        f"queued {queued}, existing active/reused {reused}."
    )
    return RedirectResponse(url=f"/admin?state_code={normalized_state}&notice={quote(notice)}", status_code=303)


@app.post("/admin/lists/{list_id}/queue")
def admin_queue_list_build(
    list_id: int,
    page: int = Form(default=1),
    state_code: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return RedirectResponse(url="/admin?error=County+list+not+found.", status_code=303)
    _, _, message = enqueue_export_job_for_list(
        db,
        obs_list=obs_list,
        requested_by="admin-single-build",
        only_if_stale=False,
        force_sync=True,
    )
    state_q = normalize_state_code(state_code or "") or ""
    query_bits = [f"page={max(1, page)}"]
    if state_q:
        query_bits.append(f"state_code={state_q}")
    query_bits.append(f"notice={quote(message)}")
    return RedirectResponse(url=f"/admin?{'&'.join(query_bits)}", status_code=303)


@app.post("/admin/lists/{list_id}/toggle-public")
def admin_toggle_public_download(
    list_id: int,
    page: int = Form(default=1),
    state_code: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return RedirectResponse(url="/admin?error=County+list+not+found.", status_code=303)
    obs_list.is_public_download = not bool(obs_list.is_public_download)
    db.commit()
    status_text = "visible" if obs_list.is_public_download else "hidden"
    notice = f"{obs_list.title} is now {status_text} on public downloads."
    state_q = normalize_state_code(state_code or "") or ""
    query_bits = [f"page={max(1, page)}"]
    if state_q:
        query_bits.append(f"state_code={state_q}")
    query_bits.append(f"notice={quote(notice)}")
    return RedirectResponse(url=f"/admin?{'&'.join(query_bits)}", status_code=303)


@app.post("/admin/lists/{list_id}/delete")
def admin_delete_list(
    list_id: int,
    page: int = Form(default=1),
    state_code: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    job_ids = [row[0] for row in db.query(models.ExportJob.id).filter_by(list_id=list_id).all()]
    _cleanup_list_export_files(job_ids, list_id)
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
    state_q = normalize_state_code(state_code or "") or ""
    query_bits = [f"page={max(1, page)}", "notice=County+list+deleted."]
    if state_q:
        query_bits.insert(1, f"state_code={state_q}")
    return RedirectResponse(url=f"/admin?{'&'.join(query_bits)}", status_code=303)


@app.post("/admin/lists/{list_id}/sync")
def admin_sync_list(
    list_id: int,
    page: int = Form(default=1),
    state_code: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return RedirectResponse(url="/admin?error=County+list+not+found.", status_code=303)

    try:
        synced = sync_list_observations(db, obs_list)
        notice = f"Sync complete for {obs_list.title}: {synced} observations."
        key = "notice"
    except Exception as exc:
        db.rollback()
        notice = f"Sync failed for {obs_list.title}: {exc}"
        key = "error"

    state_q = normalize_state_code(state_code or "") or ""
    query_bits = [f"page={max(1, page)}", f"{key}={quote(notice)}"]
    if state_q:
        query_bits.insert(1, f"state_code={state_q}")
    return RedirectResponse(url=f"/admin?{'&'.join(query_bits)}", status_code=303)


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
    _: bool = Depends(require_admin),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return template_response(
            request,
            "list.html",
            {
                "list": None,
                "observations": [],
                "error": "List not found.",
            },
            status_code=404,
        )

    title = title.strip()
    if not title:
        return template_response(
            request,
            "list.html",
            {
                "list": obs_list,
                "observations": [],
                "error": "Title is required.",
            },
            status_code=400,
        )

    user_id_int, username, user_error = parse_optional_user_filters(inat_user_id, inat_username)
    if user_error:
        return template_response(
            request,
            "list.html",
            {
                "list": obs_list,
                "observations": [],
                "error": user_error,
            },
            status_code=400,
        )

    project_id, project_error = parse_project_filter(inat_project_id)
    if project_error:
        return template_response(
            request,
            "list.html",
            {
                "list": obs_list,
                "observations": [],
                "error": project_error,
            },
            status_code=400,
        )

    if user_id_int is None and not username and not project_id:
        return template_response(
            request,
            "list.html",
            {
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
