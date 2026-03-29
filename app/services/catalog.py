from __future__ import annotations

from datetime import UTC, date, datetime
import json
import re
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.services.inat import resolve_project_filter


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


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_project_id(token: str) -> tuple[str, Optional[int], Optional[str]]:
    cleaned = (token or "").strip()
    if not cleaned:
        raise ValueError("Project ID/slug is required.")
    canonical, numeric_id, title = resolve_project_filter(cleaned)
    return canonical, numeric_id, title


def _parse_naive_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _parse_date(value: Any) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _extract_genus_key(*candidates: Optional[str]) -> Optional[str]:
    for candidate in candidates:
        text = (candidate or "").strip()
        if not text:
            continue
        for raw_token in text.split():
            token = re.sub(r"[^A-Za-z-]", "", raw_token).strip("-").lower()
            if not token:
                continue
            if token in GENUS_QUALIFIER_TOKENS:
                continue
            return token
    return None


def _configured_dna_field_id() -> str:
    candidate = str(settings.inat_dna_field_id or "").strip()
    return candidate or "2330"


def _observation_has_dna_its(obs: dict[str, Any]) -> bool:
    field_id = _configured_dna_field_id()
    for key in ("ofvs", "observation_field_values"):
        values = obs.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            obs_field = item.get("observation_field")
            obs_field_id = obs_field.get("id") if isinstance(obs_field, dict) else None
            observed_field_id = item.get("observation_field_id") or item.get("field_id") or obs_field_id
            if str(observed_field_id) != field_id:
                continue
            if str(item.get("value") or "").strip():
                return True
    return False


def flatten_observation_payload(obs: dict[str, Any]) -> Optional[dict[str, Any]]:
    inat_id = obs.get("id")
    if not isinstance(inat_id, int):
        return None

    taxon = obs.get("taxon") if isinstance(obs.get("taxon"), dict) else {}
    community = obs.get("community_taxon") if isinstance(obs.get("community_taxon"), dict) else {}
    user = obs.get("user") if isinstance(obs.get("user"), dict) else {}
    photos = obs.get("photos") if isinstance(obs.get("photos"), list) else []
    first_photo = photos[0] if photos and isinstance(photos[0], dict) else {}

    location_raw = obs.get("location")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    if isinstance(location_raw, str) and "," in location_raw:
        parts = [part.strip() for part in location_raw.split(",", 1)]
        if len(parts) == 2:
            try:
                latitude = float(parts[0])
                longitude = float(parts[1])
            except ValueError:
                latitude = None
                longitude = None

    taxon_name = str(taxon.get("name") or "").strip() or None
    species_guess = str(obs.get("species_guess") or "").strip() or None
    community_taxon_name = str(community.get("name") or "").strip() or None

    return {
        "inat_observation_id": inat_id,
        "uri": str(obs.get("uri") or obs.get("url") or f"https://www.inaturalist.org/observations/{inat_id}").strip(),
        "taxon_id": taxon.get("id") if isinstance(taxon.get("id"), int) else None,
        "taxon_name": taxon_name,
        "taxon_rank": str(taxon.get("rank") or "").strip() or None,
        "community_taxon_id": (
            obs.get("community_taxon_id")
            if isinstance(obs.get("community_taxon_id"), int)
            else (community.get("id") if isinstance(community.get("id"), int) else None)
        ),
        "community_taxon_name": community_taxon_name,
        "community_taxon_rank": str(community.get("rank") or "").strip() or None,
        "species_guess": species_guess,
        "user_login": str(user.get("login") or "").strip() or None,
        "quality_grade": str(obs.get("quality_grade") or "").strip() or None,
        "observed_on": str(obs.get("observed_on") or "").strip() or None,
        "observed_on_date": _parse_date(obs.get("observed_on")),
        "observed_at": _parse_naive_datetime(obs.get("time_observed_at")),
        "inat_created_at": _parse_naive_datetime(obs.get("created_at")),
        "inat_updated_at": _parse_naive_datetime(obs.get("updated_at")),
        "place_guess": str(obs.get("place_guess") or "").strip() or None,
        "location": str(location_raw).strip() if isinstance(location_raw, str) and location_raw.strip() else None,
        "latitude": latitude,
        "longitude": longitude,
        "geoprivacy": str(obs.get("geoprivacy") or "").strip() or None,
        "genus_key": _extract_genus_key(taxon_name, species_guess, community_taxon_name),
        "primary_photo_url": str(first_photo.get("url") or "").strip() or None,
        "primary_photo_license_code": str(first_photo.get("license_code") or "").strip() or None,
        "primary_photo_attribution": str(first_photo.get("attribution") or "").strip() or None,
        "photo_count": len([p for p in photos if isinstance(p, dict)]),
        "has_dna_its": _observation_has_dna_its(obs),
        "raw_payload": json.dumps(obs, ensure_ascii=False),
    }


def _assign_observation_fields(row: models.CatalogObservation, payload: dict[str, Any]) -> bool:
    changed = False
    for key, value in payload.items():
        if getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    if changed:
        row.updated_at = utc_now_naive()
    return changed


def sync_catalog_source(
    db: Session,
    source: models.CatalogSource,
    max_pages: int | None = None,
) -> dict[str, int]:
    per_page = 200
    max_pages_effective = max(1, max_pages or 2000)
    base = settings.inat_base_url.rstrip("/")
    url = f"{base}/observations"

    timeout = httpx.Timeout(20.0, connect=8.0)
    headers = {"User-Agent": "myDNAobv-catalog/1.0 (+https://mrdbid.com)"}

    scanned = 0
    inserted = 0
    updated = 0
    linked = 0
    seen_inat_ids: set[int] = set()

    with httpx.Client(timeout=timeout, headers=headers) as client:
        page = 1
        while page <= max_pages_effective:
            params = {
                "taxon_id": settings.inat_taxon_id,
                "project_id": source.project_id,
                "per_page": per_page,
                "page": page,
                "order_by": "id",
                "order": "asc",
            }
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            results = data.get("results") or []
            if not results:
                break

            payload_by_id: dict[int, dict[str, Any]] = {}
            for obs in results:
                if not isinstance(obs, dict):
                    continue
                flat = flatten_observation_payload(obs)
                if not flat:
                    continue
                inat_id = int(flat["inat_observation_id"])
                payload_by_id[inat_id] = flat
                seen_inat_ids.add(inat_id)
            if not payload_by_id:
                page += 1
                continue

            existing = (
                db.query(models.CatalogObservation)
                .filter(models.CatalogObservation.inat_observation_id.in_(list(payload_by_id.keys())))
                .all()
            )
            existing_by_inat = {row.inat_observation_id: row for row in existing}
            touched_rows: list[models.CatalogObservation] = []

            for inat_id, flat in payload_by_id.items():
                scanned += 1
                row = existing_by_inat.get(inat_id)
                if row is None:
                    row = models.CatalogObservation(**flat)
                    db.add(row)
                    inserted += 1
                else:
                    if _assign_observation_fields(row, flat):
                        updated += 1
                touched_rows.append(row)

            db.flush()
            observation_ids = [row.id for row in touched_rows if row.id is not None]
            if observation_ids:
                existing_links = (
                    db.query(models.CatalogObservationProject.observation_id)
                    .filter(
                        models.CatalogObservationProject.source_id == source.id,
                        models.CatalogObservationProject.observation_id.in_(observation_ids),
                    )
                    .all()
                )
                linked_obs_ids = {row[0] for row in existing_links}
                for row in touched_rows:
                    if row.id in linked_obs_ids:
                        continue
                    db.add(
                        models.CatalogObservationProject(
                            source_id=source.id,
                            observation_id=row.id,
                        )
                    )
                    linked += 1
                # Persist link rows before any cleanup query/delete runs.
                db.flush()

            total = data.get("total_results")
            if isinstance(total, int):
                total_pages = max(1, (total + per_page - 1) // per_page)
                if page >= total_pages:
                    break
            page += 1

    stale_links = (
        db.query(models.CatalogObservationProject)
        .join(models.CatalogObservation, models.CatalogObservation.id == models.CatalogObservationProject.observation_id)
        .filter(models.CatalogObservationProject.source_id == source.id)
        .all()
    )
    removed_links = 0
    for link in stale_links:
        inat_id = link.observation.inat_observation_id if link.observation else None
        if inat_id is None or inat_id in seen_inat_ids:
            continue
        db.delete(link)
        removed_links += 1

    source.last_sync_at = utc_now_naive()
    source.updated_at = utc_now_naive()
    source.last_sync_message = (
        f"Synced {scanned} observations; inserted {inserted}, updated {updated}, "
        f"linked {linked}, removed links {removed_links}."
    )

    # Ensure pending observation/link changes are in DB before orphan detection.
    db.flush()

    # Remove orphaned observations with no source links.
    orphan_ids = (
        db.query(models.CatalogObservation.id)
        .outerjoin(
            models.CatalogObservationProject,
            models.CatalogObservationProject.observation_id == models.CatalogObservation.id,
        )
        .filter(models.CatalogObservationProject.id.is_(None))
        .all()
    )
    if orphan_ids:
        db.query(models.CatalogObservation).filter(
            models.CatalogObservation.id.in_([row[0] for row in orphan_ids])
        ).delete(synchronize_session=False)

    db.commit()

    return {
        "scanned": scanned,
        "inserted": inserted,
        "updated": updated,
        "linked": linked,
        "removed_links": removed_links,
    }
