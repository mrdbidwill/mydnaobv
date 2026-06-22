from datetime import UTC, date, datetime, timedelta
from html import escape
import json
from pathlib import Path
import re
import shutil
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request as UrlRequest, urlopen
from fastapi import FastAPI, Request, Form, Depends, Query, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
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
from app.exports.publish import (
    has_latest_publish_marker,
    latest_artifact_exists,
    published_filename,
    published_job_url,
    published_latest_url,
)
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
DEFAULT_ADSENSE_CLIENT_ID = "ca-pub-8323362126637830"
CATALOG_PAGE_SIZE = max(10, min(settings.catalog_page_size, 200))
CATALOG_ALPHA_LINK_SCAN_LIMIT = 5000
GENERACOUNT_PROXY_MAX_BYTES = 2 * 1024 * 1024
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
PROJECT_REFERENCE_DATA: dict[str, dict[str, object]] = {
    "251751": {
        "name": "Alabama First Provisionals",
        "description": "Collection of sequenced specimens for species first documented in Alabama.",
        "inat_url": "https://www.inaturalist.org/observations?project_id=251751&field:DNA%20Barcode%20ITS",
        "stats_all": "Observations 160 | Species 80 | Identifiers 58 | Observers 30",
        "stats_dna": "With DNA Barcode ITS: Observations 154 | Species 80 | Identifiers 57 | Observers 30",
    },
    "124358": {
        "name": "AMS Fungal Diversity Project- Collection",
        "description": "Project for fungi being collected for the AMS Alabama Fungal Diversity Project.",
        "inat_url": "https://www.inaturalist.org/observations?project_id=124358&field:DNA%20Barcode%20ITS",
        "stats_all": "Observations 1805 | Species 557 | Identifiers 227 | Observers 24",
        "stats_dna": "With DNA Barcode ITS: Observations 430 | Species 260 | Identifiers 123 | Observers 11",
    },
    "184305": {
        "name": "Fungi of Alabama- AMS FunDiS Local Project",
        "description": "Collection project for the Fungal Diversity Survey local sequencing effort.",
        "inat_url": "https://www.inaturalist.org/observations?project_id=184305&field:DNA%20Barcode%20ITS",
        "stats_all": "Observations 1003 | Species 431 | Identifiers 164 | Observers 23",
        "stats_dna": "With DNA Barcode ITS: Observations 773 | Species 376 | Identifiers 149 | Observers 22",
    },
    "132913": {
        "name": "AMS Sequenced Specimens",
        "description": "Specimens sequenced by or for the Alabama Mushroom Society, added when splits are ready for shipment.",
        "inat_url": "https://www.inaturalist.org/observations?project_id=132913&field:DNA%20Barcode%20ITS",
        "stats_all": "Observations 2863 | Species 859 | Identifiers 301 | Observers 105",
        "stats_dna": "With DNA Barcode ITS: Observations 1632 | Species 656 | Identifiers 237 | Observers 79",
    },
}
PROJECT_REFERENCE_SNAPSHOT_LABEL = (
    "Stats snapshot: April 14, 2026 (from reference file; live iNaturalist totals may differ)."
)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse("app/static/images/favicon.svg", media_type="image/svg+xml")


def _adsense_enabled_for_runtime() -> bool:
    # AdSense disabled — set ADSENSE_ENABLED=true to re-enable
    return False
    # if "adsense_enabled" in settings.model_fields_set:
    #     return bool(settings.adsense_enabled)
    # return (settings.env or "").strip().lower() == "production"


def _adsense_publisher_id() -> str | None:
    client_id = (settings.adsense_client_id or DEFAULT_ADSENSE_CLIENT_ID).strip()
    if not client_id:
        return None
    if client_id.startswith("ca-"):
        client_id = client_id[3:]
    if not client_id.startswith("pub-"):
        return None
    return client_id


def _absolute_public_url(request: Request, path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    base = str(request.base_url).rstrip("/")
    if not path_or_url.startswith("/"):
        path_or_url = f"/{path_or_url}"
    return f"{base}{path_or_url}"


@app.get("/ads.txt", include_in_schema=False)
def ads_txt() -> PlainTextResponse:
    publisher_id = _adsense_publisher_id()
    if not publisher_id:
        raise HTTPException(status_code=404, detail="Not found")
    return PlainTextResponse(f"google.com, {publisher_id}, DIRECT, f08c47fec0942fa0")


@app.get("/robots.txt", include_in_schema=False)
def robots_txt(request: Request) -> PlainTextResponse:
    sitemap_url = _absolute_public_url(request, "/sitemap.xml")
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin",
            f"Sitemap: {sitemap_url}",
        ]
    )
    return PlainTextResponse(body)


def _sitemap_entries(request: Request, db: Session) -> list[str]:
    entries: list[str] = [
        _absolute_public_url(request, "/"),
        _absolute_public_url(request, "/methodology"),
    ]

    county_total = (
        db.query(func.count(models.ObservationList.id))
        .filter(
            models.ObservationList.product_type == "county",
            models.ObservationList.is_public_download.is_(True),
        )
        .scalar()
        or 0
    )
    county_pages = max(1, (int(county_total) + PUBLIC_COUNTY_PAGE_SIZE - 1) // PUBLIC_COUNTY_PAGE_SIZE)
    for page_number in range(2, county_pages + 1):
        entries.append(_absolute_public_url(request, f"/?page={page_number}"))

    if settings.enable_data_catalog:
        entries.append(_absolute_public_url(request, "/catalog"))
        catalog_total = (
            db.query(func.count(models.CatalogObservation.id))
            .filter(models.CatalogObservation.has_dna_its.is_(True))
            .scalar()
            or 0
        )
        catalog_pages = max(1, (int(catalog_total) + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
        for page_number in range(2, catalog_pages + 1):
            entries.append(_absolute_public_url(request, f"/catalog?page={page_number}"))

    public_lists = (
        db.query(models.ObservationList)
        .filter(models.ObservationList.is_public_download.is_(True))
        .all()
    )
    for obs_list in public_lists:
        latest_job = latest_completed_job_for_list(db, obs_list.id)
        if not latest_job:
            continue
        artifacts = list_artifacts_for_job(db, latest_job.id)
        for artifact in artifacts:
            if artifact.kind not in ("observations_index_pdf", "merged_pdf", "zip", "genera_count"):
                continue
            entries.append(
                _absolute_public_url(
                    request,
                    f"/public/lists/{obs_list.id}/artifacts/{artifact.id}/download",
                )
            )

    return sorted(set(entries))


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request, db: Session = Depends(get_db)) -> Response:
    entries = _sitemap_entries(request, db)
    url_nodes = "\n".join(f"  <url><loc>{escape(url)}</loc></url>" for url in entries)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{url_nodes}\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


def template_response(
    request: Request,
    template_name: str,
    context: dict[str, object],
    *,
    show_ads: bool = False,
    status_code: int = 200,
):
    adsense_client_id = (settings.adsense_client_id or DEFAULT_ADSENSE_CLIENT_ID).strip()
    adsense_banner_slot = (settings.adsense_banner_slot or "").strip()
    render_ads = bool(show_ads and _adsense_enabled_for_runtime() and adsense_client_id)
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


def _artifacts_by_kind(artifacts: list[models.ExportArtifact], kind: str) -> list[models.ExportArtifact]:
    return [artifact for artifact in artifacts if artifact.kind == kind]


def _format_size_label(size_bytes: int | None) -> str:
    size = max(0, int(size_bytes or 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{size} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def _download_tier_label(size_bytes: int | None) -> str | None:
    size = max(0, int(size_bytes or 0))
    if size >= 1024 * 1024 * 1024:
        return "Very large download; best on desktop/Wi-Fi."
    if size >= 250 * 1024 * 1024:
        return "Large download; best on desktop/Wi-Fi."
    return None


def _download_meta(artifact: models.ExportArtifact | None) -> dict[str, str] | None:
    if not artifact:
        return None
    return {
        "size_label": _format_size_label(artifact.size_bytes),
        "tier_label": _download_tier_label(artifact.size_bytes) or "",
    }


def _project_display_title(obs_list: models.ObservationList) -> str:
    raw_title = (obs_list.title or "").strip()
    for marker in ("— iNaturalist Project", "- iNaturalist Project"):
        if marker in raw_title:
            candidate = raw_title.split(marker, 1)[0].strip(" -")
            if candidate:
                return candidate
    if raw_title:
        return raw_title
    if obs_list.inat_project_id:
        return f"Project {obs_list.inat_project_id}"
    return f"Project list {obs_list.id}"


def _project_reference(project_id: str | None) -> dict[str, object] | None:
    token = (project_id or "").strip()
    if not token:
        return None
    digits_only = "".join(ch for ch in token if ch.isdigit())
    key = digits_only or token
    payload = PROJECT_REFERENCE_DATA.get(key)
    if not payload:
        return None
    merged = dict(payload)
    merged.setdefault("snapshot_label", PROJECT_REFERENCE_SNAPSHOT_LABEL)
    return merged


def _artifact_public_url(list_id: int, artifact: models.ExportArtifact | None) -> str | None:
    if not artifact:
        return None
    # Always route public downloads through the app endpoint so local-file
    # fallback remains available when object-store "latest" links are stale.
    return f"/public/lists/{list_id}/artifacts/{artifact.id}/download"


def _artifact_public_download_url(list_id: int, artifact: models.ExportArtifact | None) -> str | None:
    if not artifact:
        return None
    return f"/public/lists/{list_id}/artifacts/{artifact.id}/download?download=1"


def _fetch_published_genera_count_text(url: str) -> str:
    req = UrlRequest(url, headers={"User-Agent": "myDNAobv-public-download/1.0"})
    try:
        with urlopen(req, timeout=20) as response:
            data = response.read(GENERACOUNT_PROXY_MAX_BYTES + 1)
    except (TimeoutError, URLError, OSError) as exc:
        raise HTTPException(status_code=404, detail="File not available") from exc

    if len(data) > GENERACOUNT_PROXY_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    return data.decode("utf-8", errors="replace")


def _fetch_published_latest_manifest(list_id: int) -> dict[str, object] | None:
    base_url = (settings.export_publish_base_url or "").strip()
    if not base_url:
        return None
    manifest_url = f"{base_url.rstrip('/')}/list_{list_id}/latest/manifest.json"
    req = UrlRequest(manifest_url, headers={"User-Agent": "myDNAobv-public-download/1.0"})
    try:
        with urlopen(req, timeout=10) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
    except (HTTPError, TimeoutError, URLError, OSError):
        return None
    if len(payload) > 2 * 1024 * 1024:
        return None
    try:
        decoded = json.loads(payload.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if isinstance(decoded, dict):
        return decoded
    return None


def _published_latest_manifest_fallback_url(list_id: int, artifact_kind: str) -> str | None:
    manifest = _fetch_published_latest_manifest(list_id)
    if not manifest:
        return None
    files = manifest.get("files")
    if not isinstance(files, list):
        return None

    base_url = (settings.export_publish_base_url or "").strip().rstrip("/")
    if not base_url:
        return None

    for row in files:
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "").strip() != artifact_kind:
            continue
        filename = str(row.get("filename") or "").strip()
        if not filename:
            continue
        return f"{base_url}/list_{list_id}/latest/{filename}"
    return None


def _published_url_available(url: str) -> bool:
    req = UrlRequest(url, method="HEAD", headers={"User-Agent": "myDNAobv-public-download/1.0"})
    try:
        with urlopen(req, timeout=10):
            return True
    except HTTPError as exc:
        if exc.code != 405:
            return False
    except (TimeoutError, URLError, OSError):
        return False

    # Some object-store/CDN setups block HEAD; fallback to GET probe.
    get_req = UrlRequest(url, headers={"User-Agent": "myDNAobv-public-download/1.0"})
    try:
        with urlopen(get_req, timeout=10):
            return True
    except (HTTPError, TimeoutError, URLError, OSError):
        return False


def _legacy_latest_redirect_allowed(list_id: int, artifact: models.ExportArtifact) -> bool:
    if artifact.kind == "zip_chunk":
        return False
    return not has_latest_publish_marker(list_id)


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


def _format_utc_timestamp(value: datetime | None) -> str:
    utc_value = as_utc(value)
    if not utc_value:
        return "Unknown"
    return utc_value.strftime("%Y-%m-%d %H:%M UTC")


def _refresh_summary(
    last_sync_at: datetime | None,
    *,
    latest_completed_job: models.ExportJob | None = None,
    active_refresh_job: models.ExportJob | None = None,
) -> dict[str, object]:
    if not last_sync_at:
        return {
            "last_refreshed_label": "Not refreshed yet",
            "next_refresh_label": "Refresh pending",
            "is_due": True,
            "status_line": "",
        }

    last_sync_utc = as_utc(last_sync_at)
    now_utc = as_utc(utc_now_naive())
    if not last_sync_utc or not now_utc:
        return {
            "last_refreshed_label": "Not refreshed yet",
            "next_refresh_label": "Refresh pending",
            "is_due": True,
            "status_line": "",
        }

    next_due = last_sync_utc + timedelta(days=PUBLIC_REFRESH_INTERVAL_DAYS)
    is_due = now_utc >= next_due
    status_line = ""

    if active_refresh_job:
        job_message = (active_refresh_job.message or "").strip()
        if (
            active_refresh_job.status == "waiting_quota"
            and ("HTTP 429" in job_message or "throttling" in job_message.lower())
        ):
            status_line = (
                "Refresh is delayed; latest downloads remain available while "
                "background retries continue."
            )
        elif active_refresh_job.status in ("queued", "running"):
            status_line = "Refresh update is in progress."

    if not status_line and is_due and latest_completed_job:
        latest_message = (latest_completed_job.message or "").strip()
        if "Sync deferred (" in latest_message:
            status_line = (
                "Latest update used cached observations; "
                "a fresh refresh is still running in the background."
            )
        else:
            latest_completed_at = as_utc(latest_completed_job.finished_at) or as_utc(latest_completed_job.started_at)
            if latest_completed_at and latest_completed_at > last_sync_utc:
                status_line = (
                    "Latest update completed from cached observations while a fresh refresh "
                    "is still pending."
                )

    return {
        "last_refreshed_label": _format_utc_date(last_sync_utc),
        "next_refresh_label": _format_utc_date(next_due),
        "is_due": is_due,
        "status_line": status_line,
    }


def _latest_active_refresh_job_for_list(db: Session, list_id: int) -> models.ExportJob | None:
    return (
        db.query(models.ExportJob)
        .filter(
            models.ExportJob.list_id == list_id,
            models.ExportJob.status.in_(("queued", "running", "waiting_quota")),
        )
        .order_by(models.ExportJob.id.desc())
        .first()
    )


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
        genera_file_download_url = None
        zip_chunk_downloads: list[dict[str, str]] = []
        status_label = "Not built"
        if latest_job:
            artifacts = list_artifacts_for_job(db, latest_job.id)
            county_artifact = _preferred_county_file_artifact(artifacts)
            index_artifact = _artifact_by_kind(artifacts, "observations_index_pdf")
            genera_artifact = _artifact_by_kind(artifacts, "genera_count")
            zip_chunk_artifacts = sorted(
                _artifacts_by_kind(artifacts, "zip_chunk"),
                key=lambda item: (item.part_number or 0, item.id),
            )
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
            genera_file_download_url = _artifact_public_download_url(obs_list.id, genera_artifact)
            zip_chunk_downloads = []
            for chunk in zip_chunk_artifacts:
                chunk_url = _artifact_public_url(obs_list.id, chunk)
                if not chunk_url:
                    continue
                chunk_path = artifact_abspath(chunk)
                chunk_available = (
                    (chunk_path.exists() and chunk_path.is_file())
                    or latest_artifact_exists(obs_list.id, chunk)
                )
                if not chunk_available:
                    continue
                zip_chunk_downloads.append(
                    {
                        "label": f"Part {chunk.part_number}",
                        "url": chunk_url,
                        "size_label": _format_size_label(chunk.size_bytes),
                    }
                )
        active_refresh_job = _latest_active_refresh_job_for_list(db, obs_list.id)
        refresh_data = _refresh_summary(
            obs_list.last_sync_at,
            latest_completed_job=latest_job,
            active_refresh_job=active_refresh_job,
        )

        catalog.append(
            {
                "list": obs_list,
                "latest_job": latest_job,
                "active_refresh_job": active_refresh_job,
                "county_artifact": county_artifact,
                "index_artifact": index_artifact,
                "genera_artifact": genera_artifact,
                "status_label": status_label,
                "county_download_url": county_download_url,
                "county_download_label": county_download_label,
                "county_download_meta": _download_meta(county_artifact),
                "index_download_url": index_download_url,
                "index_download_meta": _download_meta(index_artifact),
                "genera_download_url": genera_download_url,
                "genera_file_download_url": genera_file_download_url,
                "genera_download_meta": _download_meta(genera_artifact),
                "zip_chunk_downloads": zip_chunk_downloads,
                "last_refreshed_label": refresh_data["last_refreshed_label"],
                "next_refresh_label": refresh_data["next_refresh_label"],
                "refresh_due": refresh_data["is_due"],
                "refresh_status_line": refresh_data["status_line"],
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
        genera_file_download_url = None
        zip_chunk_downloads: list[dict[str, str]] = []
        status_label = "Not built"

        if latest_job:
            status_label = latest_job.status
        if latest_ready:
            artifacts = list_artifacts_for_job(db, latest_ready.id)
            county_artifact = _preferred_county_file_artifact(artifacts)
            index_artifact = _artifact_by_kind(artifacts, "observations_index_pdf")
            genera_artifact = _artifact_by_kind(artifacts, "genera_count")
            zip_chunk_artifacts = sorted(
                _artifacts_by_kind(artifacts, "zip_chunk"),
                key=lambda item: (item.part_number or 0, item.id),
            )
            is_index_only = getattr(obs_list, "export_mode", "full") == "index_only"
            if (county_artifact and index_artifact) or (is_index_only and index_artifact):
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
            genera_file_download_url = _artifact_public_download_url(obs_list.id, genera_artifact)
            zip_chunk_downloads = []
            for chunk in zip_chunk_artifacts:
                chunk_url = _artifact_public_url(obs_list.id, chunk)
                if not chunk_url:
                    continue
                chunk_path = artifact_abspath(chunk)
                chunk_available = (
                    (chunk_path.exists() and chunk_path.is_file())
                    or latest_artifact_exists(obs_list.id, chunk)
                )
                if not chunk_available:
                    continue
                zip_chunk_downloads.append(
                    {
                        "label": f"Part {chunk.part_number}",
                        "url": chunk_url,
                        "size_label": _format_size_label(chunk.size_bytes),
                    }
                )
        active_refresh_job = _latest_active_refresh_job_for_list(db, obs_list.id)
        refresh_data = _refresh_summary(
            obs_list.last_sync_at,
            latest_completed_job=latest_ready,
            active_refresh_job=active_refresh_job,
        )
        out.append(
            {
                "list": obs_list,
                "display_title": _project_display_title(obs_list),
                "project_reference": _project_reference(obs_list.inat_project_id),
                "latest_job": latest_job,
                "active_refresh_job": active_refresh_job,
                "county_artifact": county_artifact,
                "index_artifact": index_artifact,
                "genera_artifact": genera_artifact,
                "status_label": status_label,
                "county_download_url": county_download_url,
                "county_download_label": county_download_label,
                "county_download_meta": _download_meta(county_artifact),
                "index_download_url": index_download_url,
                "index_download_meta": _download_meta(index_artifact),
                "genera_download_url": genera_download_url,
                "genera_file_download_url": genera_file_download_url,
                "genera_download_meta": _download_meta(genera_artifact),
                "zip_chunk_downloads": zip_chunk_downloads,
                "last_refreshed_label": refresh_data["last_refreshed_label"],
                "next_refresh_label": refresh_data["next_refresh_label"],
                "refresh_due": refresh_data["is_due"],
                "refresh_status_line": refresh_data["status_line"],
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


@app.get("/methodology")
def methodology_page(request: Request):
    return template_response(
        request,
        "methodology.html",
        {
            "title": "Data Methodology",
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

    if dna_only:
        filtered_query = filtered_query.filter(models.CatalogObservation.has_dna_its.is_(True))

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

    total = filtered_query.count()
    pages = max(1, (total + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
    current_page = min(page, pages)
    rows = (
        base_query
        .offset((current_page - 1) * CATALOG_PAGE_SIZE)
        .limit(CATALOG_PAGE_SIZE)
        .all()
    )

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

    alpha_page_links: list[dict[str, object]] = []
    alpha_links_skipped = False
    if alpha_column is not None:
        if total > CATALOG_ALPHA_LINK_SCAN_LIMIT:
            alpha_links_skipped = True
        else:
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
                        "is_current": target_page is not None and int(target_page) == int(current_page),
                    }
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
            "alpha_links_skipped": alpha_links_skipped,
            "alpha_links_scan_limit": CATALOG_ALPHA_LINK_SCAN_LIMIT,
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

    if dna_only:
        filtered_query = filtered_query.filter(models.CatalogObservation.has_dna_its.is_(True))

    rows = (
        filtered_query
        .with_entities(
            models.CatalogObservation.taxon_name,
            models.CatalogObservation.species_guess,
            models.CatalogObservation.community_taxon_name,
            models.CatalogObservation.genus_key,
        )
        .all()
    )

    counts_by_genus: dict[str, int] = {}
    labels_by_genus: dict[str, str] = {}
    total_observations = 0
    for taxon_name, species_guess, community_taxon_name, genus_key in rows:
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
    if latest_artifact_exists(job.list_id, artifact) or _legacy_latest_redirect_allowed(job.list_id, artifact):
        published_url = published_latest_url(job.list_id, artifact)
        if published_url:
            return RedirectResponse(url=published_url, status_code=307)

    raise HTTPException(status_code=404, detail="File not available")


@app.get("/public/lists/{list_id}/artifacts/{artifact_id}/download")
def public_download_latest_artifact(
    list_id: int,
    artifact_id: int,
    download: bool = False,
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
    if artifact.kind not in ("merged_pdf", "zip", "zip_chunk", "observations_index_pdf", "genera_count"):
        raise HTTPException(status_code=404, detail="Artifact not public")

    artifact_path = artifact_abspath(artifact)
    if artifact_path.exists() and artifact_path.is_file():
        if artifact.kind == "genera_count":
            disposition = "attachment" if download else "inline"
            return FileResponse(
                path=str(artifact_path),
                filename=artifact_path.name,
                media_type="text/plain; charset=utf-8",
                content_disposition_type=disposition,
            )
        return FileResponse(path=str(artifact_path), filename=artifact_path.name)

    published_url = None
    latest_url = published_latest_url(list_id, artifact)
    if latest_url and (
        latest_artifact_exists(list_id, artifact) or _legacy_latest_redirect_allowed(list_id, artifact)
    ):
        if _published_url_available(latest_url):
            published_url = latest_url
        else:
            job_url = published_job_url(list_id, latest_job.id, artifact)
            if job_url and _published_url_available(job_url):
                published_url = job_url
            else:
                compat_url = _published_latest_manifest_fallback_url(list_id, artifact.kind)
                if compat_url and _published_url_available(compat_url):
                    published_url = compat_url

    if published_url:
        if download and artifact.kind == "genera_count":
            text_body = _fetch_published_genera_count_text(published_url)
            safe_name = quote(published_filename(artifact))
            headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}"}
            return PlainTextResponse(text_body, headers=headers)
        return RedirectResponse(url=published_url, status_code=307)

    raise HTTPException(status_code=404, detail="File not available")


@app.get("/admin/queue-status")
def admin_queue_status(
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """
    JSON snapshot of export queue health for operational monitoring.

    Key fields to watch:
    - total_active: should be > 0 while the cron is running normally
    - oldest_waiting_quota_next_run: if many hours ago, jobs may be stuck
    - list_health.overdue: number of public lists past their refresh window with no active job
    - disk_free_gb: alert if this drops below 10
    - last_completed_job.finished_at: should be within the last few hours during active runs
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    refresh_cutoff = now - timedelta(days=max(1, settings.public_refresh_interval_days))

    # Status counts across all jobs
    status_rows = (
        db.query(models.ExportJob.status, func.count(models.ExportJob.id))
        .group_by(models.ExportJob.status)
        .all()
    )
    by_status = {row[0]: row[1] for row in status_rows}

    # Oldest waiting_quota next_run_at
    oldest_waiting = (
        db.query(models.ExportJob.next_run_at)
        .filter(models.ExportJob.status == "waiting_quota")
        .order_by(models.ExportJob.next_run_at.asc().nullsfirst())
        .limit(1)
        .scalar()
    )

    # Most recently completed job
    last_completed = (
        db.query(models.ExportJob.finished_at, models.ExportJob.list_id, models.ExportJob.id)
        .filter(models.ExportJob.status.in_(("ready", "partial_ready")))
        .order_by(models.ExportJob.finished_at.desc().nullslast())
        .limit(1)
        .first()
    )

    # Disk free on export storage dir
    disk_free_gb: float | None = None
    try:
        usage = shutil.disk_usage(settings.export_storage_dir)
        disk_free_gb = round(usage.free / (1024 ** 3), 2)
    except OSError:
        pass

    # Oldest active queued job (age of queue backlog)
    oldest_queued_at = (
        db.query(func.min(models.ExportJob.created_at))
        .filter(models.ExportJob.status.in_(("queued", "waiting_quota")))
        .scalar()
    )

    # Lists that currently have an active job (queued/running/waiting_quota)
    active_list_ids = {
        row[0]
        for row in db.query(models.ExportJob.list_id)
        .filter(models.ExportJob.status.in_(("queued", "running", "waiting_quota")))
        .distinct()
        .all()
        if row[0] is not None
    }

    # Public lists: freshness breakdown
    public_lists = (
        db.query(models.ObservationList)
        .filter(
            models.ObservationList.is_public_download.is_(True),
            models.ObservationList.product_type.in_(("county", "project")),
        )
        .all()
    )

    # Latest completed job per public list
    public_list_ids = {int(ol.id) for ol in public_lists if ol.id is not None}
    latest_jobs_subq = (
        db.query(
            models.ExportJob.list_id.label("list_id"),
            func.max(models.ExportJob.id).label("job_id"),
        )
        .filter(
            models.ExportJob.list_id.in_(public_list_ids),
            models.ExportJob.status.in_(("ready", "partial_ready")),
        )
        .group_by(models.ExportJob.list_id)
        .subquery()
    )
    latest_jobs = {
        int(j.list_id): j
        for j in db.query(models.ExportJob)
        .join(latest_jobs_subq, models.ExportJob.id == latest_jobs_subq.c.job_id)
        .all()
        if j.list_id is not None
    }

    never_exported_count = 0
    fresh_count = 0
    overdue_count = 0
    overdue_active_count = 0  # overdue but already queued/running
    oldest_export_dt: datetime | None = None

    for ol in public_lists:
        list_id = int(ol.id)
        job = latest_jobs.get(list_id)
        if job is None:
            never_exported_count += 1
            continue
        last_sync = job.finished_at
        if last_sync and last_sync.tzinfo is not None:
            last_sync = last_sync.astimezone(UTC).replace(tzinfo=None)
        if last_sync is None or last_sync <= refresh_cutoff:
            if list_id in active_list_ids:
                overdue_active_count += 1
            else:
                overdue_count += 1
        else:
            fresh_count += 1
        if last_sync is not None:
            if oldest_export_dt is None or last_sync < oldest_export_dt:
                oldest_export_dt = last_sync

    # Worker activity: check the run log if it exists
    worker_log_path = Path(settings.export_storage_dir) / "worker_runs.log"
    last_worker_run: str | None = None
    try:
        if worker_log_path.exists():
            lines = worker_log_path.read_text(encoding="utf-8").strip().splitlines()
            last_worker_run = lines[-1] if lines else None
    except OSError:
        pass

    return {
        "as_of": now.isoformat(),
        "by_status": by_status,
        "total_active": sum(by_status.get(s, 0) for s in ("queued", "running", "waiting_quota")),
        "oldest_waiting_quota_next_run": oldest_waiting.isoformat() if oldest_waiting else None,
        "oldest_queued_created_at": oldest_queued_at.isoformat() if oldest_queued_at else None,
        "last_completed_job": {
            "id": last_completed[2],
            "list_id": last_completed[1],
            "finished_at": last_completed[0].isoformat() if last_completed[0] else None,
        } if last_completed else None,
        "list_health": {
            "total_public": len(public_lists),
            "fresh": fresh_count,
            "overdue_queued": overdue_active_count,
            "overdue_idle": overdue_count,
            "never_exported": never_exported_count,
            "oldest_export_at": oldest_export_dt.isoformat() if oldest_export_dt else None,
        },
        "disk_free_gb": disk_free_gb,
        "last_worker_run": last_worker_run,
        "config": {
            "export_run_timeout_seconds": settings.export_run_timeout_seconds,
            "sync_max_concurrent": settings.export_sync_max_concurrent,
            "refresh_interval_days": settings.public_refresh_interval_days,
            "request_interval_seconds": settings.export_request_interval_seconds,
            "defer_to_cache_products": settings.export_sync_defer_to_cache_products,
            "storage_pressure_min_free_gb": settings.export_storage_pressure_min_free_gb,
        },
    }


@app.get("/admin/list-health")
def admin_list_health(
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    """
    Per-list export freshness for all public county and project lists.
    Returns one row per list showing last export date, status, and whether it is overdue.
    Use this to find specific counties that have not been updated recently.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    refresh_interval = timedelta(days=max(1, settings.public_refresh_interval_days))
    refresh_cutoff = now - refresh_interval

    public_lists = (
        db.query(models.ObservationList)
        .filter(
            models.ObservationList.is_public_download.is_(True),
            models.ObservationList.product_type.in_(("county", "project")),
        )
        .order_by(
            models.ObservationList.product_type.asc(),
            models.ObservationList.state_code.asc().nullslast(),
            models.ObservationList.county_name.asc().nullslast(),
            models.ObservationList.title.asc(),
        )
        .all()
    )

    public_list_ids = {int(ol.id) for ol in public_lists if ol.id is not None}

    # Latest completed job per list
    latest_jobs_subq = (
        db.query(
            models.ExportJob.list_id.label("list_id"),
            func.max(models.ExportJob.id).label("job_id"),
        )
        .filter(
            models.ExportJob.list_id.in_(public_list_ids),
            models.ExportJob.status.in_(("ready", "partial_ready")),
        )
        .group_by(models.ExportJob.list_id)
        .subquery()
    )
    latest_jobs = {
        int(j.list_id): j
        for j in db.query(models.ExportJob)
        .join(latest_jobs_subq, models.ExportJob.id == latest_jobs_subq.c.job_id)
        .all()
        if j.list_id is not None
    }

    # Active job per list (queued/running/waiting_quota)
    active_jobs_by_list: dict[int, models.ExportJob] = {}
    for j in (
        db.query(models.ExportJob)
        .filter(
            models.ExportJob.list_id.in_(public_list_ids),
            models.ExportJob.status.in_(("queued", "running", "waiting_quota")),
        )
        .order_by(models.ExportJob.created_at.asc())
        .all()
    ):
        if j.list_id is not None and int(j.list_id) not in active_jobs_by_list:
            active_jobs_by_list[int(j.list_id)] = j

    rows = []
    for ol in public_lists:
        list_id = int(ol.id)
        job = latest_jobs.get(list_id)
        active = active_jobs_by_list.get(list_id)

        last_exported_at: str | None = None
        last_export_status: str | None = None
        days_since_export: float | None = None
        is_overdue = False

        if job is not None:
            finished = job.finished_at
            if finished and finished.tzinfo is not None:
                finished = finished.astimezone(UTC).replace(tzinfo=None)
            if finished is not None:
                last_exported_at = finished.isoformat()
                days_since_export = round((now - finished).total_seconds() / 86400, 1)
                is_overdue = finished <= refresh_cutoff
            last_export_status = job.status

        active_info = None
        if active is not None:
            active_info = {
                "job_id": active.id,
                "status": active.status,
                "phase": active.phase,
                "next_run_at": active.next_run_at.isoformat() if active.next_run_at else None,
            }

        rows.append({
            "list_id": list_id,
            "title": ol.title,
            "product_type": ol.product_type,
            "county": ol.county_name,
            "state": ol.state_code,
            "last_exported_at": last_exported_at,
            "last_export_status": last_export_status,
            "days_since_export": days_since_export,
            "is_overdue": is_overdue,
            "active_job": active_info,
        })

    overdue = [r for r in rows if r["is_overdue"] and r["active_job"] is None]
    never = [r for r in rows if r["last_exported_at"] is None]

    return {
        "as_of": now.isoformat(),
        "refresh_interval_days": settings.public_refresh_interval_days,
        "summary": {
            "total": len(rows),
            "fresh": len([r for r in rows if not r["is_overdue"] and r["last_exported_at"]]),
            "overdue_idle": len(overdue),
            "overdue_queued": len([r for r in rows if r["is_overdue"] and r["active_job"]]),
            "never_exported": len(never),
        },
        "overdue_idle": overdue,
        "never_exported": never,
        "all": rows,
    }


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
    state_code: str = Form(...),
    description_prefix: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
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
                models.ObservationList.place_query == row.place_query,
            )
            .first()
        )
        if existing:
            skipped_existing += 1
            continue

        title = f"{row.county_name}-{normalized_state}"
        description_parts = [
            "Auto-generated county list.",
            f"Place: {row.place_query}.",
            "Queries all AMS projects for DNA Barcode ITS observations.",
        ]
        if description_prefix_clean:
            description_parts.insert(0, description_prefix_clean)

        obs_list = models.ObservationList(
            title=title,
            description=" ".join(description_parts),
            inat_user_id=None,
            inat_username=None,
            inat_project_id=None,
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
        f"County seeding complete for {normalized_state}: "
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
        title = title_main
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
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    normalized_state = normalize_state_code(state_code or "")
    if not normalized_state:
        return RedirectResponse(url="/admin?error=Please+select+a+valid+US+state.", status_code=303)

    targets = (
        db.query(models.ObservationList)
        .filter(
            models.ObservationList.product_type == "county",
            models.ObservationList.state_code == normalized_state,
        )
        .all()
    )

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
