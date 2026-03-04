from datetime import datetime
from typing import Any, Iterable, Optional
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


def _fetch_user_detail_by_id(client: httpx.Client, base: str, user_id: int) -> Optional[dict]:
    try:
        resp = client.get(f"{base}/users/{user_id}")
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    results = data.get("results") or []
    if results and isinstance(results[0], dict):
        return results[0]
    return None


def _find_user_by_login(client: httpx.Client, base: str, login: str) -> Optional[dict]:
    normalized = (login or "").strip().lower()
    if not normalized:
        return None

    try:
        resp = client.get(f"{base}/users/autocomplete", params={"q": login, "per_page": 30})
        resp.raise_for_status()
        data = resp.json()
        for row in data.get("results") or []:
            if not isinstance(row, dict):
                continue
            candidate = str(row.get("login") or "").strip().lower()
            if candidate == normalized:
                return row
    except Exception:
        pass

    try:
        resp = client.get(f"{base}/users/{login}")
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if results and isinstance(results[0], dict):
            row = results[0]
            candidate = str(row.get("login") or "").strip().lower()
            if candidate == normalized:
                return row
    except Exception:
        return None

    return None


def _resolve_place_id(client: httpx.Client, base: str, query: str) -> tuple[Optional[int], Optional[str]]:
    place_query = (query or "").strip()
    if not place_query:
        return None, None

    try:
        resp = client.get(f"{base}/places/autocomplete", params={"q": place_query, "per_page": 20})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None, None

    query_tokens = [token.strip().lower() for token in place_query.replace(",", " ").split() if token.strip()]
    best_score = -1
    best_row: Optional[dict[str, Any]] = None

    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("display_name") or row.get("name") or "").strip()
        if not label:
            continue
        label_lower = label.lower()
        score = sum(1 for token in query_tokens if token in label_lower)
        if "county" in query_tokens and "county" in label_lower:
            score += 2
        if score > best_score:
            best_score = score
            best_row = row

    if not best_row:
        return None, None

    place_id = best_row.get("id")
    if place_id is None:
        return None, None
    return int(place_id), str(best_row.get("display_name") or best_row.get("name") or "")


def fetch_observations_for_list(obs_list: models.ObservationList) -> Iterable[InatObservation]:
    """
    Placeholder for iNaturalist API integration.

    Intended filters:
    - user_id or user login
    - county/address (resolved to place_id)
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
        normalized_username = (obs_list.inat_username or "").strip()
        resolved_user_id: Optional[int] = obs_list.inat_user_id

        if resolved_user_id is None and not normalized_username:
            raise ValueError("Provide an iNaturalist user ID or username.")

        if resolved_user_id is not None:
            detail = _fetch_user_detail_by_id(client, base, int(resolved_user_id))
            if detail:
                canonical_login = str(detail.get("login") or "").strip()
                canonical_id = detail.get("id")
                if canonical_id is not None:
                    resolved_user_id = int(canonical_id)
                    obs_list.inat_user_id = resolved_user_id
                if canonical_login:
                    if normalized_username and canonical_login.lower() != normalized_username.lower():
                        raise ValueError(
                            "Provided iNaturalist user ID and username do not match iNaturalist records."
                        )
                    obs_list.inat_username = canonical_login
                    normalized_username = canonical_login
            elif normalized_username:
                # If ID lookup is unavailable, still validate username against iNaturalist.
                user_row = _find_user_by_login(client, base, normalized_username)
                if not user_row:
                    raise ValueError("Could not verify iNaturalist username.")
                row_id = user_row.get("id")
                row_login = str(user_row.get("login") or "").strip()
                if row_id is not None and int(row_id) != int(resolved_user_id):
                    raise ValueError(
                        "Provided iNaturalist user ID and username do not match iNaturalist records."
                    )
                if row_login:
                    obs_list.inat_username = row_login
                    normalized_username = row_login
        else:
            user_row = _find_user_by_login(client, base, normalized_username)
            if not user_row:
                raise ValueError("Could not find iNaturalist username.")
            row_id = user_row.get("id")
            row_login = str(user_row.get("login") or "").strip()
            if row_id is not None:
                resolved_user_id = int(row_id)
                obs_list.inat_user_id = resolved_user_id
            if row_login:
                obs_list.inat_username = row_login
                normalized_username = row_login

        resolved_place_id: Optional[int] = obs_list.inat_place_id
        place_query = (obs_list.place_query or "").strip()
        if place_query:
            place_id, place_name = _resolve_place_id(client, base, place_query)
            if place_id is None:
                raise ValueError(
                    "Could not resolve county/address filter. Try a clearer place such as 'Baldwin County, Alabama'."
                )
            resolved_place_id = int(place_id)
            obs_list.inat_place_id = resolved_place_id
            if place_name:
                obs_list.place_query = place_query

        while page <= max_pages:
            params = {
                "taxon_id": settings.inat_taxon_id,
                "per_page": per_page,
                "page": page,
                "order_by": "observed_on",
                "order": "desc",
            }
            if resolved_user_id is not None:
                params["user_id"] = resolved_user_id
            elif normalized_username:
                params["user_login"] = normalized_username
            if settings.inat_dna_field_name:
                # iNaturalist search URL syntax supports field filters.
                params[f"field:{settings.inat_dna_field_name}"] = ""
            if obs_list.taxon_filter:
                params["taxon_name"] = obs_list.taxon_filter.strip()
            if resolved_place_id is not None:
                params["place_id"] = resolved_place_id

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
