from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import re
import shutil
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse
from fastapi import FastAPI, Request, Form, Depends, Query, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
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
from app.services.catalog import normalize_project_id, sync_catalog_source


templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
security = HTTPBasic()


PAGE_SIZE = 10
OBS_PAGE_SIZE = 15
ADMIN_PAGE_SIZE = 25
PUBLIC_COUNTY_PAGE_SIZE = 24
PUBLIC_REFRESH_INTERVAL_DAYS = max(1, settings.public_refresh_interval_days)
DEFAULT_PROJECT_BUILD_IDS = "124358\n184305\n132913\n251751"
CATALOG_PAGE_SIZE = max(10, min(settings.catalog_page_size, 200))
GENUS_QUALIFIER_TOKENS = {
    "cf",
    "aff",
    "nr",
    "sp",
    "spp",
    "complex",
    "group",
    "sect",
    "subsp",
    "var",
    "forma",
}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse("app/static/images/favicon.svg", media_type="image/svg+xml")


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


def normalize_catalog_sort(sort: str | None) -> str:
    candidate = (sort or "").strip().lower()
    if candidate in (
        "observed_desc",
        "observed_asc",
        "genus_asc",
        "taxon_asc",
        "community_taxon_asc",
        "observed_taxon_asc",
        "place_asc",
        "updated_desc",
    ):
        return candidate
    return "observed_desc"


def ensure_data_catalog_enabled() -> None:
    if not settings.enable_data_catalog:
        raise HTTPException(status_code=404, detail="Not found")


def parse_optional_date(raw: str | None) -> tuple[Optional[date], Optional[str]]:
    text = (raw or "").strip()
    if not text:
        return None, None
    try:
        return date.fromisoformat(text), None
    except ValueError:
        return None, "Use YYYY-MM-DD date format."


def _extract_genus_label_from_text(value: str | None) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    for raw_token in text.split():
        cleaned = re.sub(r"[^A-Za-z-]", "", raw_token).strip("-")
        if not cleaned:
            continue
        if cleaned.lower() in GENUS_QUALIFIER_TOKENS:
            continue
        return cleaned
    return None


def _catalog_genus_label(
    taxon_name: str | None,
    species_guess: str | None,
    community_taxon_name: str | None,
    genus_key: str | None,
) -> Optional[str]:
    for value in (taxon_name, species_guess, community_taxon_name, genus_key):
        label = _extract_genus_label_from_text(value)
        if label:
            return label
    return None


def _alpha_initial(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "#"
    initial = text[0].upper()
    if "A" <= initial <= "Z":
        return initial
    return "#"


def _payload_has_dna_its(raw_payload: str | None) -> bool:
    if not raw_payload:
        return False
    try:
        payload = json.loads(raw_payload)
    except Exception:
        return False

    field_id = str(settings.inat_dna_field_id or "2330").strip() or "2330"
    for key in ("ofvs", "observation_field_values"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            obs_field = item.get("observation_field")
            obs_field_id = obs_field.get("id") if isinstance(obs_field, dict) else None
            ofid = item.get("observation_field_id") or item.get("field_id") or obs_field_id
            if str(ofid) != field_id:
                continue
            if str(item.get("value") or "").strip():
                return True
    return False


def _catalog_alpha_value(row: models.CatalogObservation, normalized_sort: str) -> str | None:
    if normalized_sort == "taxon_asc":
        return row.taxon_name
    if normalized_sort == "community_taxon_asc":
        return row.community_taxon_name
    if normalized_sort == "observed_taxon_asc":
        return row.species_guess
    if normalized_sort == "genus_asc":
        return row.genus_key
    if normalized_sort == "place_asc":
        return row.place_guess
    return None


def _build_catalog_filtered_query(
    db: Session,
    source_id: int,
    genus: str,
    query: str,
    from_date: Optional[date],
    to_date: Optional[date],
) -> tuple[object, Optional[models.CatalogSource]]:
    filtered_query = db.query(models.CatalogObservation)
    selected_source = None
    if source_id > 0:
        selected_source = db.query(models.CatalogSource).filter_by(id=source_id).first()
        filtered_query = filtered_query.join(
            models.CatalogObservationProject,
            models.CatalogObservationProject.observation_id == models.CatalogObservation.id,
        ).filter(models.CatalogObservationProject.source_id == source_id)

    cleaned_genus = (genus or "").strip().lower()
    if cleaned_genus:
        filtered_query = filtered_query.filter(models.CatalogObservation.genus_key.like(f"{cleaned_genus}%"))

    cleaned_query = (query or "").strip()
    if cleaned_query:
        needle = f"%{cleaned_query}%"
        filtered_query = filtered_query.filter(
            or_(
                models.CatalogObservation.taxon_name.ilike(needle),
                models.CatalogObservation.species_guess.ilike(needle),
                models.CatalogObservation.community_taxon_name.ilike(needle),
                models.CatalogObservation.place_guess.ilike(needle),
                models.CatalogObservation.user_login.ilike(needle),
            )
        )

    if from_date:
        filtered_query = filtered_query.filter(models.CatalogObservation.observed_on_date >= from_date)
    if to_date:
        filtered_query = filtered_query.filter(models.CatalogObservation.observed_on_date <= to_date)

    return filtered_query, selected_source


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
        genera_artifact = None
        county_download_url = None
        county_download_label = None
        index_download_url = None
        genera_download_url = None
        status_label = "Not built"
        if latest_job:
            artifacts = list_artifacts_for_job(db, latest_job.id)
            county_artifact = _preferred_county_file_artifact(artifacts)
            index_artifact = _artifact_by_kind(artifacts, "observations_index_pdf")
            genera_artifact = _artifact_by_kind(artifacts, "genera_count")
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
            genera_download_url = _artifact_public_url(obs_list.id, genera_artifact)

        refresh_data = _refresh_summary(obs_list.last_sync_at)

        catalog.append(
            {
                "list": obs_list,
                "latest_job": latest_job,
                "county_artifact": county_artifact,
                "index_artifact": index_artifact,
                "genera_artifact": genera_artifact,
                "status_label": status_label,
                "county_download_url": county_download_url,
                "county_download_label": county_download_label,
                "index_download_url": index_download_url,
                "genera_download_url": genera_download_url,
                "last_refreshed_label": refresh_data["last_refreshed_label"],
                "next_refresh_label": refresh_data["next_refresh_label"],
                "refresh_due": refresh_data["is_due"],
            }
        )

    return catalog, pages, current_page, state_options, normalized_state


def load_public_project_rows(db: Session) -> list[dict[str, object]]:
    project_lists = (
        db.query(models.ObservationList)
        .filter(
            models.ObservationList.product_type == "project",
            models.ObservationList.is_public_download.is_(True),
        )
        .order_by(models.ObservationList.title.asc(), models.ObservationList.id.asc())
        .all()
    )

    out: list[dict[str, object]] = []
    for obs_list in project_lists:
        latest_jobs = list_jobs_for_list(db, obs_list.id, limit=1)
        latest_job = latest_jobs[0] if latest_jobs else None
        latest_ready = latest_completed_job_for_list(db, obs_list.id)

        county_artifact = None
        index_artifact = None
        genera_artifact = None
        county_download_url = None
        county_download_label = None
        index_download_url = None
        genera_download_url = None
        status_label = "Not built"

        if latest_job:
            status_label = latest_job.status
        if latest_ready:
            artifacts = list_artifacts_for_job(db, latest_ready.id)
            county_artifact = _preferred_county_file_artifact(artifacts)
            index_artifact = _artifact_by_kind(artifacts, "observations_index_pdf")
            genera_artifact = _artifact_by_kind(artifacts, "genera_count")
            if county_artifact and index_artifact:
                status_label = "Ready"
            elif county_artifact:
                status_label = "Project file ready"
            elif index_artifact:
                status_label = "Observation index ready"

            county_download_url = _artifact_public_url(obs_list.id, county_artifact)
            if county_artifact:
                county_download_label = "Project PDF" if county_artifact.kind == "merged_pdf" else "Project ZIP"
            index_download_url = _artifact_public_url(obs_list.id, index_artifact)
            genera_download_url = _artifact_public_url(obs_list.id, genera_artifact)

        refresh_data = _refresh_summary(obs_list.last_sync_at)
        out.append(
            {
                "list": obs_list,
                "latest_job": latest_job,
                "county_artifact": county_artifact,
                "index_artifact": index_artifact,
                "genera_artifact": genera_artifact,
                "status_label": status_label,
                "county_download_url": county_download_url,
                "county_download_label": county_download_label,
                "index_download_url": index_download_url,
                "genera_download_url": genera_download_url,
                "last_refreshed_label": refresh_data["last_refreshed_label"],
                "next_refresh_label": refresh_data["next_refresh_label"],
                "refresh_due": refresh_data["is_due"],
            }
        )

    return out


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


def _normalize_project_seed_token(raw_line: str) -> str:
    token = (raw_line or "").strip()
    if not token:
        return ""

    if token.lower().startswith("project #"):
        token = token.split("#", 1)[1].strip()

    if token.startswith("http://") or token.startswith("https://"):
        parsed = urlparse(token)
        query_project = parse_qs(parsed.query).get("project_id")
        if query_project:
            token = (query_project[0] or "").strip()
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] == "projects":
                token = parts[1].strip()

    return token.strip().strip(",;")


def parse_project_seed_values(raw: str) -> tuple[list[str], Optional[str]]:
    values: list[str] = []
    seen: set[str] = set()

    for line in (raw or "").replace(",", "\n").splitlines():
        candidate = _normalize_project_seed_token(line)
        if not candidate:
            continue
        parsed, error = parse_project_filter(candidate)
        if error:
            return [], f"Invalid project ID/slug '{candidate}': {error}"
        if not parsed:
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        values.append(parsed)

    if not values:
        return [], "Provide at least one iNaturalist project ID/slug (one per line)."
    return values, None


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
    project_rows = load_public_project_rows(db)

    return template_response(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "data_catalog_enabled": settings.enable_data_catalog,
            "rows": rows,
            "project_rows": project_rows,
            "page": current_page,
            "pages": pages,
            "state_options": state_options,
            "state_code": normalized_state,
        },
        show_ads=True,
    )


@app.get("/catalog")
def catalog_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    source_id: int = Query(default=0, ge=0),
    genus: str = Query(default=""),
    query: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    sort: str = Query(default="taxon_asc"),
    db: Session = Depends(get_db),
):
    ensure_data_catalog_enabled()
    dna_only = True

    normalized_sort = normalize_catalog_sort(sort)
    from_date, from_error = parse_optional_date(date_from)
    to_date, to_error = parse_optional_date(date_to)
    date_error = from_error or to_error

    filtered_query, selected_source = _build_catalog_filtered_query(
        db,
        source_id,
        genus,
        query,
        from_date,
        to_date,
    )

    base_query = filtered_query

    if normalized_sort == "observed_asc":
        base_query = base_query.order_by(
            models.CatalogObservation.observed_on_date.asc().nullslast(),
            models.CatalogObservation.inat_observation_id.asc(),
        )
    elif normalized_sort == "genus_asc":
        base_query = base_query.order_by(
            models.CatalogObservation.genus_key.asc().nullslast(),
            models.CatalogObservation.observed_on_date.desc().nullslast(),
            models.CatalogObservation.inat_observation_id.desc(),
        )
    elif normalized_sort == "taxon_asc":
        base_query = base_query.order_by(
            models.CatalogObservation.taxon_name.asc().nullslast(),
            models.CatalogObservation.observed_on_date.desc().nullslast(),
            models.CatalogObservation.inat_observation_id.desc(),
        )
    elif normalized_sort == "community_taxon_asc":
        base_query = base_query.order_by(
            models.CatalogObservation.community_taxon_name.asc().nullslast(),
            models.CatalogObservation.observed_on_date.desc().nullslast(),
            models.CatalogObservation.inat_observation_id.desc(),
        )
    elif normalized_sort == "observed_taxon_asc":
        base_query = base_query.order_by(
            models.CatalogObservation.species_guess.asc().nullslast(),
            models.CatalogObservation.observed_on_date.desc().nullslast(),
            models.CatalogObservation.inat_observation_id.desc(),
        )
    elif normalized_sort == "place_asc":
        base_query = base_query.order_by(
            models.CatalogObservation.place_guess.asc().nullslast(),
            models.CatalogObservation.observed_on_date.desc().nullslast(),
            models.CatalogObservation.inat_observation_id.desc(),
        )
    elif normalized_sort == "updated_desc":
        base_query = base_query.order_by(
            models.CatalogObservation.inat_updated_at.desc().nullslast(),
            models.CatalogObservation.inat_observation_id.desc(),
        )
    else:
        base_query = base_query.order_by(
            models.CatalogObservation.observed_on_date.desc().nullslast(),
            models.CatalogObservation.inat_observation_id.desc(),
        )

    alpha_page_links: list[dict[str, object]] = []
    if dna_only:
        ordered_rows = base_query.all()
        filtered_rows = [row for row in ordered_rows if _payload_has_dna_its(row.raw_payload)]
        total = len(filtered_rows)
        pages = max(1, (total + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
        current_page = min(page, pages)
        start = (current_page - 1) * CATALOG_PAGE_SIZE
        rows = filtered_rows[start : start + CATALOG_PAGE_SIZE]

        alpha_enabled = normalized_sort in {
            "taxon_asc",
            "community_taxon_asc",
            "observed_taxon_asc",
            "genus_asc",
            "place_asc",
        }
        if alpha_enabled:
            letter_to_page: dict[str, int] = {}
            for idx, row in enumerate(filtered_rows, start=1):
                letter = _alpha_initial(_catalog_alpha_value(row, normalized_sort))
                if letter not in letter_to_page:
                    letter_to_page[letter] = ((idx - 1) // CATALOG_PAGE_SIZE) + 1

            for letter in list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["#"]:
                target_page = letter_to_page.get(letter)
                alpha_page_links.append(
                    {
                        "letter": letter,
                        "page": target_page,
                        "is_current": target_page is not None and int(target_page) == int(current_page),
                    }
                )
    else:
        total = filtered_query.count()
        alpha_column = None
        if normalized_sort == "taxon_asc":
            alpha_column = models.CatalogObservation.taxon_name
        elif normalized_sort == "community_taxon_asc":
            alpha_column = models.CatalogObservation.community_taxon_name
        elif normalized_sort == "observed_taxon_asc":
            alpha_column = models.CatalogObservation.species_guess
        elif normalized_sort == "genus_asc":
            alpha_column = models.CatalogObservation.genus_key
        elif normalized_sort == "place_asc":
            alpha_column = models.CatalogObservation.place_guess

        if alpha_column is not None:
            ordered_values = base_query.with_entities(alpha_column).all()
            letter_to_page: dict[str, int] = {}
            for idx, (value,) in enumerate(ordered_values, start=1):
                letter = _alpha_initial(value)
                if letter not in letter_to_page:
                    letter_to_page[letter] = ((idx - 1) // CATALOG_PAGE_SIZE) + 1

            for letter in list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["#"]:
                target_page = letter_to_page.get(letter)
                alpha_page_links.append(
                    {
                        "letter": letter,
                        "page": target_page,
                        "is_current": target_page is not None and int(target_page) == int(page),
                    }
                )

        pages = max(1, (total + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
        current_page = min(page, pages)
        if alpha_page_links:
            for item in alpha_page_links:
                target_page = item.get("page")
                item["is_current"] = target_page is not None and int(target_page) == int(current_page)
        rows = (
            base_query
            .offset((current_page - 1) * CATALOG_PAGE_SIZE)
            .limit(CATALOG_PAGE_SIZE)
            .all()
        )

    sources = (
        db.query(models.CatalogSource)
        .order_by(models.CatalogSource.project_title.asc().nullslast(), models.CatalogSource.project_id.asc())
        .all()
    )
    by_source_id = {source.id: source for source in sources}

    memberships_by_obs: dict[int, list[str]] = {}
    if rows:
        obs_ids = [row.id for row in rows]
        memberships = (
            db.query(models.CatalogObservationProject)
            .filter(models.CatalogObservationProject.observation_id.in_(obs_ids))
            .all()
        )
        for membership in memberships:
            source_row = by_source_id.get(membership.source_id)
            if not source_row:
                continue
            label = (source_row.project_title or source_row.project_id or "").strip() or source_row.project_id
            memberships_by_obs.setdefault(membership.observation_id, []).append(label)

    return template_response(
        request,
        "catalog.html",
        {
            "rows": rows,
            "page": current_page,
            "pages": pages,
            "total": total,
            "sources": sources,
            "selected_source_id": source_id,
            "selected_source": selected_source,
            "genus": genus,
            "query": query,
            "date_from": date_from,
            "date_to": date_to,
            "sort": normalized_sort,
            "dna_only": dna_only,
            "date_error": date_error,
            "memberships_by_obs": memberships_by_obs,
            "alpha_page_links": alpha_page_links,
        },
        show_ads=True,
    )


@app.get("/catalog/genera-count")
def catalog_genera_count(
    source_id: int = Query(default=0, ge=0),
    genus: str = Query(default=""),
    query: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    db: Session = Depends(get_db),
):
    ensure_data_catalog_enabled()
    dna_only = True

    from_date, from_error = parse_optional_date(date_from)
    to_date, to_error = parse_optional_date(date_to)
    if from_error or to_error:
        raise HTTPException(status_code=400, detail=from_error or to_error)

    filtered_query, selected_source = _build_catalog_filtered_query(
        db,
        source_id,
        genus,
        query,
        from_date,
        to_date,
    )

    rows = (
        filtered_query
        .with_entities(
            models.CatalogObservation.taxon_name,
            models.CatalogObservation.species_guess,
            models.CatalogObservation.community_taxon_name,
            models.CatalogObservation.genus_key,
            models.CatalogObservation.raw_payload,
        )
        .all()
    )

    counts_by_genus: dict[str, int] = {}
    labels_by_genus: dict[str, str] = {}
    total_observations = 0
    for taxon_name, species_guess, community_taxon_name, genus_key, raw_payload in rows:
        if dna_only and not _payload_has_dna_its(raw_payload):
            continue
        total_observations += 1
        label = _catalog_genus_label(taxon_name, species_guess, community_taxon_name, genus_key)
        if not label:
            continue
        key = label.lower()
        labels_by_genus.setdefault(key, label)
        counts_by_genus[key] = counts_by_genus.get(key, 0) + 1

    sorted_items = sorted(counts_by_genus.items(), key=lambda item: (labels_by_genus[item[0]].lower(), item[0]))
    source_label = (
        (selected_source.project_title or selected_source.project_id).strip()
        if selected_source and (selected_source.project_title or selected_source.project_id)
        else "All sources"
    )

    lines = [
        "Catalog Genera Count (DNA Barcode ITS only)" if dna_only else "Catalog Genera Count",
        f"Source filter: {source_label}",
        f"Filters: genus='{genus or ''}', query='{query or ''}', date_from='{date_from or ''}', date_to='{date_to or ''}'",
        f"DNA Barcode ITS filter: {'on' if dna_only else 'off'}",
        f"Total observations considered: {total_observations}",
        f"Total unique genera: {len(sorted_items)}",
        "",
    ]
    for idx, (key, count) in enumerate(sorted_items, start=1):
        lines.append(f"{idx}. {labels_by_genus[key]} ({count})")

    filename = "catalog_genera_count.txt"
    if selected_source and selected_source.project_id:
        slug = re.sub(r"[^a-z0-9-]+", "-", selected_source.project_id.lower()).strip("-")
        if slug:
            filename = f"{slug}_catalog_genera_count.txt"
    if dna_only:
        filename = filename.replace(".txt", "_dna_its.txt")

    return PlainTextResponse(
        "\n".join(lines),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/admin/catalog")
def admin_catalog_page(
    request: Request,
    notice: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    ensure_data_catalog_enabled()

    sources = (
        db.query(models.CatalogSource)
        .order_by(models.CatalogSource.project_title.asc().nullslast(), models.CatalogSource.project_id.asc())
        .all()
    )
    counts = dict(
        db.query(
            models.CatalogObservationProject.source_id,
            func.count(models.CatalogObservationProject.id),
        )
        .group_by(models.CatalogObservationProject.source_id)
        .all()
    )
    return template_response(
        request,
        "admin_catalog.html",
        {
            "notice": notice,
            "error": error,
            "sources": sources,
            "observation_count_by_source": counts,
        },
    )


@app.post("/admin/catalog/sources")
def admin_catalog_add_source(
    project_id_or_slug: str = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    ensure_data_catalog_enabled()

    token = (project_id_or_slug or "").strip()
    if not token:
        return RedirectResponse(
            url="/admin/catalog?error=Provide+an+iNaturalist+project+ID+or+slug.",
            status_code=303,
        )

    try:
        canonical, numeric_id, title = normalize_project_id(token)
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/catalog?error={quote(str(exc))}",
            status_code=303,
        )

    existing = db.query(models.CatalogSource).filter_by(project_id=canonical).first()
    if existing:
        existing.project_numeric_id = numeric_id
        existing.project_title = title or existing.project_title
        existing.is_active = True
        existing.updated_at = utc_now_naive()
        db.commit()
        return RedirectResponse(
            url=f"/admin/catalog?notice={quote(f'Source {canonical} already existed; reactivated.')}",
            status_code=303,
        )

    db.add(
        models.CatalogSource(
            project_id=canonical,
            project_numeric_id=numeric_id,
            project_title=title,
            is_active=True,
        )
    )
    db.commit()
    return RedirectResponse(
        url=f"/admin/catalog?notice={quote(f'Added source {canonical}.')}",
        status_code=303,
    )


@app.post("/admin/catalog/sources/{source_id}/sync")
def admin_catalog_sync_source(
    source_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    ensure_data_catalog_enabled()

    source = db.query(models.CatalogSource).filter_by(id=source_id).first()
    if not source:
        return RedirectResponse(url="/admin/catalog?error=Catalog+source+not+found.", status_code=303)
    if not source.is_active:
        return RedirectResponse(url="/admin/catalog?error=Catalog+source+is+inactive.", status_code=303)

    try:
        summary = sync_catalog_source(db, source, max_pages=settings.catalog_sync_max_pages)
    except Exception as exc:
        db.rollback()
        return RedirectResponse(
            url=f"/admin/catalog?error={quote(f'Sync failed for {source.project_id}: {exc}')}",
            status_code=303,
        )

    message = (
        f"Synced {source.project_id}: scanned {summary['scanned']}, inserted {summary['inserted']}, "
        f"updated {summary['updated']}, linked {summary['linked']}, removed links {summary['removed_links']}."
    )
    return RedirectResponse(url=f"/admin/catalog?notice={quote(message)}", status_code=303)


@app.post("/admin/catalog/sources/sync-all")
def admin_catalog_sync_all_sources(
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    ensure_data_catalog_enabled()

    sources = (
        db.query(models.CatalogSource)
        .filter(models.CatalogSource.is_active.is_(True))
        .order_by(models.CatalogSource.id.asc())
        .all()
    )
    if not sources:
        return RedirectResponse(url="/admin/catalog?error=No+active+catalog+sources+found.", status_code=303)

    synced = 0
    failed = 0
    for source in sources:
        try:
            sync_catalog_source(db, source, max_pages=settings.catalog_sync_max_pages)
            synced += 1
        except Exception:
            db.rollback()
            failed += 1
            continue

    return RedirectResponse(
        url=f"/admin/catalog?notice={quote(f'Sync-all finished: synced {synced}, failed {failed}.')}",
        status_code=303,
    )


@app.post("/admin/catalog/sources/{source_id}/delete")
def admin_catalog_delete_source(
    source_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    ensure_data_catalog_enabled()

    source = db.query(models.CatalogSource).filter_by(id=source_id).first()
    if not source:
        return RedirectResponse(url="/admin/catalog?error=Catalog+source+not+found.", status_code=303)

    source_label = source.project_id
    db.query(models.CatalogObservationProject).filter_by(source_id=source.id).delete(synchronize_session=False)
    db.delete(source)

    orphan_ids = (
        db.query(models.CatalogObservation.id)
        .outerjoin(
            models.CatalogObservationProject,
            models.CatalogObservationProject.observation_id == models.CatalogObservation.id,
        )
        .filter(models.CatalogObservationProject.id.is_(None))
        .all()
    )
    removed_orphans = 0
    if orphan_ids:
        removed_orphans = len(orphan_ids)
        db.query(models.CatalogObservation).filter(
            models.CatalogObservation.id.in_([row[0] for row in orphan_ids])
        ).delete(synchronize_session=False)

    db.commit()
    return RedirectResponse(
        url=f"/admin/catalog?notice={quote(f'Deleted source {source_label}; removed {removed_orphans} orphan observations.')}",
        status_code=303,
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


@app.get("/admin/jobs/{job_id}/artifacts/{artifact_id}/download")
def admin_download_export_artifact_by_job(
    job_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
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
    if artifact_path.exists() and artifact_path.is_file():
        return FileResponse(path=str(artifact_path), filename=artifact_path.name)

    # Fallback to published latest URL when local retention cleanup removed job files.
    if latest_artifact_exists(job.list_id, artifact):
        published_url = published_latest_url(job.list_id, artifact)
        if published_url:
            return RedirectResponse(url=published_url, status_code=307)

    raise HTTPException(status_code=404, detail="File not available")


@app.get("/public/lists/{list_id}/artifacts/{artifact_id}/download")
def public_download_latest_artifact(
    list_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        raise HTTPException(status_code=404, detail="List not found")
    if obs_list.product_type not in ("county", "project") or not obs_list.is_public_download:
        raise HTTPException(status_code=404, detail="Not found")

    latest_job = latest_completed_job_for_list(db, list_id)
    if not latest_job:
        raise HTTPException(status_code=404, detail="No completed export available")

    artifact = get_artifact_for_job(db, job_id=latest_job.id, artifact_id=artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.kind not in ("merged_pdf", "zip", "observations_index_pdf", "genera_count"):
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
    county_query = db.query(models.ObservationList).filter(
        models.ObservationList.product_type == "county",
    )
    if normalized_state:
        county_query = county_query.filter(models.ObservationList.state_code == normalized_state)

    total = county_query.count()
    pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    current_page = min(page, pages)

    lists = (
        county_query
        .order_by(
            models.ObservationList.state_code.asc().nullslast(),
            models.ObservationList.county_name.asc().nullslast(),
            models.ObservationList.title.asc(),
        )
        .offset((current_page - 1) * ADMIN_PAGE_SIZE)
        .limit(ADMIN_PAGE_SIZE)
        .all()
    )

    project_lists = (
        db.query(models.ObservationList)
        .filter(models.ObservationList.product_type == "project")
        .order_by(models.ObservationList.title.asc(), models.ObservationList.id.asc())
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
        ready_download_url_by_list[obs_list.id] = (
            f"/admin/jobs/{latest_ready.id}/artifacts/{chosen.id}/download"
        )

    project_latest_job_by_list: dict[int, models.ExportJob] = {}
    project_ready_download_url_by_list: dict[int, str] = {}
    for obs_list in project_lists:
        recent_jobs = list_jobs_for_list(db, obs_list.id, limit=1)
        if recent_jobs:
            project_latest_job_by_list[obs_list.id] = recent_jobs[0]

        latest_ready = latest_completed_job_for_list(db, obs_list.id)
        if not latest_ready:
            continue
        artifacts = list_artifacts_for_job(db, latest_ready.id)
        chosen = _preferred_county_file_artifact(artifacts)
        if not chosen:
            continue
        project_ready_download_url_by_list[obs_list.id] = (
            f"/admin/jobs/{latest_ready.id}/artifacts/{chosen.id}/download"
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
            "data_catalog_enabled": settings.enable_data_catalog,
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
            "default_project_build_ids": DEFAULT_PROJECT_BUILD_IDS,
            "latest_job_by_list": latest_job_by_list,
            "ready_download_url_by_list": ready_download_url_by_list,
            "project_lists": project_lists,
            "project_latest_job_by_list": project_latest_job_by_list,
            "project_ready_download_url_by_list": project_ready_download_url_by_list,
            "export_sort_taxon_source": (settings.export_sort_taxon_source or "observation").strip().lower(),
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


@app.post("/admin/projects/build")
def admin_seed_and_build_projects(
    project_ids: str = Form(...),
    description_prefix: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    seed_values, seed_error = parse_project_seed_values(project_ids)
    if seed_error:
        return RedirectResponse(url=f"/admin?error={quote(seed_error)}", status_code=303)

    description_prefix_clean = (description_prefix or "").strip()
    created = 0
    reused_existing = 0
    queued = 0
    reused_queue = 0
    errors: list[str] = []
    target_lists: list[models.ObservationList] = []

    for project_value in seed_values:
        try:
            canonical_project_id, _, project_title = resolve_project_filter(project_value)
        except Exception as exc:
            errors.append(f"{project_value}: {exc}")
            continue

        existing = (
            db.query(models.ObservationList)
            .filter(
                models.ObservationList.product_type == "project",
                models.ObservationList.inat_project_id == canonical_project_id,
            )
            .first()
        )
        if existing:
            if not existing.is_public_download:
                existing.is_public_download = True
                db.flush()
            reused_existing += 1
            target_lists.append(existing)
            continue

        title_main = project_title or f"Project {canonical_project_id}"
        title = f"{title_main} — iNaturalist Project {canonical_project_id}"
        description_parts = [
            "Auto-generated project list.",
            f"Project: {canonical_project_id}.",
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
            product_type="project",
            state_code=None,
            county_name=None,
            is_public_download=True,
            inat_place_id=None,
            place_query=None,
            inat_dna_field_id=settings.inat_dna_field_id,
            taxon_filter=None,
        )
        db.add(obs_list)
        db.flush()
        created += 1
        target_lists.append(obs_list)

    db.commit()

    for obs_list in target_lists:
        _, was_queued, _ = enqueue_export_job_for_list(
            db,
            obs_list=obs_list,
            requested_by="admin-project-seed",
            only_if_stale=False,
            force_sync=True,
        )
        if was_queued:
            queued += 1
        else:
            reused_queue += 1

    if errors and not target_lists:
        return RedirectResponse(
            url=f"/admin?error={quote('Project build failed: ' + '; '.join(errors))}",
            status_code=303,
        )

    notice = (
        f"Project build request complete: submitted {len(seed_values)}, created {created}, "
        f"reused existing {reused_existing}. Queue: queued {queued}, already active/reused {reused_queue}."
    )
    if errors:
        notice += f" Skipped {len(errors)} invalid/unresolved project entries."
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
    query_bits = [f"page={max(1, page)}", "notice=List+deleted."]
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
