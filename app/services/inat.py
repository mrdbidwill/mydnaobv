from datetime import datetime
from typing import Iterable, Optional
import httpx
from app.core.config import settings
from app import models


class InatObservation:
    def __init__(
        self,
        inat_id: int,
        taxon_name: Optional[str],
        species_guess: Optional[str],
        scientific_name: Optional[str],
        common_name: Optional[str],
        user_name: Optional[str],
        observed_at: Optional[datetime],
        inat_url: str,
        dna_field_value: Optional[str],
        photo_url: Optional[str],
        photo_license_code: Optional[str],
        photo_attribution: Optional[str],
    ):
        self.inat_id = inat_id
        self.taxon_name = taxon_name
        self.species_guess = species_guess
        self.scientific_name = scientific_name
        self.common_name = common_name
        self.user_name = user_name
        self.observed_at = observed_at
        self.inat_url = inat_url
        self.dna_field_value = dna_field_value
        self.photo_url = photo_url
        self.photo_license_code = photo_license_code
        self.photo_attribution = photo_attribution


def _extract_field_value(obs: dict, field_id: str) -> Optional[str]:
    candidates = (
        obs.get("ofvs"),
        obs.get("observation_field_values"),
    )
    for group in candidates:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            of_id = (
                item.get("observation_field_id")
                or item.get("field_id")
                or (item.get("observation_field") or {}).get("id")
            )
            if of_id is None:
                continue
            if str(of_id) == str(field_id):
                value = item.get("value")
                if value is not None:
                    return str(value)
    return None


def _parse_observed_at(obs: dict) -> Optional[datetime]:
    for key in ("time_observed_at", "observed_on", "observed_on_string"):
        raw = obs.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _fetch_observation_detail(client: httpx.Client, base: str, obs_id: int) -> Optional[dict]:
    try:
        resp = client.get(f"{base}/observations/{obs_id}")
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    results = data.get("results") or []
    if results and isinstance(results[0], dict):
        return results[0]
    return None


def _extract_primary_photo(obs: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    photos = obs.get("photos")
    if not isinstance(photos, list) or not photos:
        return None, None, None
    photo = photos[0]
    if not isinstance(photo, dict):
        return None, None, None

    url = (
        photo.get("large_url")
        or photo.get("medium_url")
        or photo.get("url")
        or (photo.get("sizes") or {}).get("large")
        or (photo.get("sizes") or {}).get("medium")
    )
    if isinstance(url, str):
        url = url.replace("square.", "large.")
    license_code = photo.get("license_code")
    attribution = photo.get("attribution")
    return (
        str(url) if url else None,
        str(license_code) if license_code else None,
        str(attribution) if attribution else None,
    )


def fetch_observations_for_list(obs_list: models.ObservationList) -> Iterable[InatObservation]:
    """
    Placeholder for iNaturalist API integration.

    Intended filters:
    - user_id (numeric)
    - observation field: DNA Barcode ITS
    - taxon: Fungi (default: 47170)

    Returns an iterable of InatObservation objects.
    """
    base = settings.inat_base_url.rstrip("/")
    if not obs_list.inat_dna_field_id:
        return []

    url = f"{base}/observations"
    per_page = 200
    page = 1
    max_pages = 200

    timeout = httpx.Timeout(10.0, connect=5.0)
    headers = {"User-Agent": "myDNAobv/1.0 (+https://mrdbid.com)"}

    max_items = max(1, settings.max_observations)
    found = 0

    def matches_taxon(
        taxon_name: Optional[str], species_guess: Optional[str], scientific_name: Optional[str]
    ) -> bool:
        if not obs_list.taxon_filter:
            return True
        needle = obs_list.taxon_filter.strip().lower()
        if not needle:
            return True
        for value in (taxon_name, species_guess, scientific_name):
            if value and value.lower().startswith(needle):
                return True
        return False

    with httpx.Client(timeout=timeout, headers=headers) as client:
        while page <= max_pages:
            params = {
                "user_id": obs_list.inat_user_id,
                "taxon_id": settings.inat_taxon_id,
                "per_page": per_page,
                "page": page,
                "order_by": "observed_on",
                "order": "desc",
            }
            if settings.inat_dna_field_name:
                # iNaturalist search URL syntax supports field filters.
                params[f"field:{settings.inat_dna_field_name}"] = ""
            if obs_list.taxon_filter:
                params["taxon_name"] = obs_list.taxon_filter.strip()

            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            results = data.get("results") or []
            if not results:
                break

            for obs in results:
                if not isinstance(obs, dict):
                    continue
                field_value = _extract_field_value(obs, obs_list.inat_dna_field_id)
                if field_value is None:
                    detail = _fetch_observation_detail(client, base, int(obs.get("id", 0)))
                    if detail:
                        field_value = _extract_field_value(detail, obs_list.inat_dna_field_id)
                if field_value is None:
                    continue

                inat_id = obs.get("id")
                if inat_id is None:
                    continue

                taxon = obs.get("taxon") or {}
                taxon_name = taxon.get("name") or obs.get("taxon_name")
                species_guess = obs.get("species_guess")
                scientific_name = taxon.get("name") or obs.get("scientific_name")
                common_name = (taxon.get("preferred_common_name") or taxon.get("common_name"))
                user = obs.get("user") or {}
                user_name = user.get("name") or user.get("login")
                inat_url = obs.get("uri") or obs.get("url") or f"https://www.inaturalist.org/observations/{inat_id}"
                observed_at = _parse_observed_at(obs)
                photo_url, photo_license_code, photo_attribution = _extract_primary_photo(obs)
                if photo_url is None:
                    detail = _fetch_observation_detail(client, base, int(inat_id))
                    if detail:
                        photo_url, photo_license_code, photo_attribution = _extract_primary_photo(detail)

                if not matches_taxon(taxon_name, species_guess, scientific_name):
                    continue

                yield InatObservation(
                    inat_id=int(inat_id),
                    taxon_name=taxon_name,
                    species_guess=species_guess,
                    scientific_name=scientific_name,
                    common_name=common_name,
                    user_name=user_name,
                    observed_at=observed_at,
                    inat_url=inat_url,
                    dna_field_value=field_value,
                    photo_url=photo_url,
                    photo_license_code=photo_license_code,
                    photo_attribution=photo_attribution,
                )
                found += 1
                if found >= max_items:
                    return

            total = data.get("total_results")
            if isinstance(total, int):
                total_pages = max(1, (total + per_page - 1) // per_page)
                if page >= total_pages:
                    break

            page += 1
