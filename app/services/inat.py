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
        observation_taxon_id: Optional[int],
        observation_taxon_name: Optional[str],
        observation_taxon_rank: Optional[str],
        community_taxon_id: Optional[int],
        community_taxon_name: Optional[str],
        community_taxon_rank: Optional[str],
        user_name: Optional[str],
        observed_at: Optional[datetime],
        inat_url: str,
        dna_field_value: Optional[str],
        photo_url: Optional[str],
        photo_license_code: Optional[str],
        photo_attribution: Optional[str],
        photo_entries: list["InatPhoto"],
    ):
        self.inat_id = inat_id
        self.taxon_name = taxon_name
        self.species_guess = species_guess
        self.scientific_name = scientific_name
        self.common_name = common_name
        self.observation_taxon_id = observation_taxon_id
        self.observation_taxon_name = observation_taxon_name
        self.observation_taxon_rank = observation_taxon_rank
        self.community_taxon_id = community_taxon_id
        self.community_taxon_name = community_taxon_name
        self.community_taxon_rank = community_taxon_rank
        self.user_name = user_name
        self.observed_at = observed_at
        self.inat_url = inat_url
        self.dna_field_value = dna_field_value
        self.photo_url = photo_url
        self.photo_license_code = photo_license_code
        self.photo_attribution = photo_attribution
        self.photo_entries = photo_entries


class InatPhoto:
    def __init__(
        self,
        inat_photo_id: Optional[int],
        photo_index: int,
        photo_url: str,
        photo_license_code: Optional[str],
        photo_attribution: Optional[str],
    ):
        self.inat_photo_id = inat_photo_id
        self.photo_index = photo_index
        self.photo_url = photo_url
        self.photo_license_code = photo_license_code
        self.photo_attribution = photo_attribution


def _queryable_dna_field_name() -> Optional[str]:
    candidate = (settings.inat_dna_field_name or "").strip()
    if not candidate:
        return None
    # Protect against malformed env values accidentally concatenating another key.
    if "=" in candidate:
        return "DNA Barcode ITS"
    return candidate


def _split_project_filter_values(raw: Optional[str]) -> list[str]:
    text = str(raw or "").replace("\n", ",")
    values: list[str] = []
    seen: set[str] = set()
    for token in text.split(","):
        cleaned = token.strip()
        if not cleaned:
            continue
        marker = cleaned.lower()
        if marker in seen:
            continue
        seen.add(marker)
        values.append(cleaned)
    return values


def _project_filters_for_list(obs_list: models.ObservationList) -> list[str]:
    if (obs_list.product_type or "").strip().lower() == "county":
        configured = _split_project_filter_values(settings.inat_county_project_ids)
        if configured:
            return configured
    return _split_project_filter_values(obs_list.inat_project_id)


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


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _extract_taxon_summary(taxon: Any) -> tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
    if not isinstance(taxon, dict):
        return None, None, None, None
    taxon_id = _coerce_int(taxon.get("id"))
    taxon_name = str(taxon.get("name") or "").strip() or None
    taxon_rank = str(taxon.get("rank") or "").strip() or None
    common_name = str(taxon.get("preferred_common_name") or taxon.get("common_name") or "").strip() or None
    return taxon_id, taxon_name, taxon_rank, common_name


def _pick_observation_taxon_from_identifications(
    identifications: Any,
    observer_user_id: Optional[int],
) -> tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
    if not isinstance(identifications, list):
        return None, None, None, None

    def taxon_from_ident(predicate) -> tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
        for ident in identifications:
            if not isinstance(ident, dict):
                continue
            if not predicate(ident):
                continue
            taxon_id, taxon_name, taxon_rank, common_name = _extract_taxon_summary(ident.get("taxon"))
            if taxon_name:
                return taxon_id, taxon_name, taxon_rank, common_name
        return None, None, None, None

    def ident_user_id(ident: dict) -> Optional[int]:
        user = ident.get("user")
        if not isinstance(user, dict):
            return None
        return _coerce_int(user.get("id"))

    pick_order = (
        lambda i: observer_user_id is not None and bool(i.get("current")) and bool(i.get("own_observation")) and ident_user_id(i) == observer_user_id,
        lambda i: observer_user_id is not None and bool(i.get("current")) and ident_user_id(i) == observer_user_id,
        lambda i: observer_user_id is not None and bool(i.get("own_observation")) and ident_user_id(i) == observer_user_id,
        lambda i: bool(i.get("current")),
    )

    for predicate in pick_order:
        picked = taxon_from_ident(predicate)
        if picked[1]:
            return picked
    return None, None, None, None


def _extract_taxa(obs: dict, detail: Optional[dict] = None) -> dict[str, Optional[str] | Optional[int]]:
    observer_user_id = _coerce_int(((obs.get("user") or {}).get("id")))

    obs_taxon_id, obs_taxon_name, obs_taxon_rank, obs_common_name = _pick_observation_taxon_from_identifications(
        obs.get("identifications"),
        observer_user_id,
    )
    if not obs_taxon_name and detail:
        obs_taxon_id, obs_taxon_name, obs_taxon_rank, obs_common_name = _pick_observation_taxon_from_identifications(
            detail.get("identifications"),
            observer_user_id,
        )

    comm_taxon_id = _coerce_int(obs.get("community_taxon_id"))
    comm_taxon_obj = obs.get("community_taxon") if isinstance(obs.get("community_taxon"), dict) else None
    if not comm_taxon_obj and detail:
        detail_comm = detail.get("community_taxon")
        if isinstance(detail_comm, dict):
            comm_taxon_obj = detail_comm
        if comm_taxon_id is None:
            comm_taxon_id = _coerce_int(detail.get("community_taxon_id"))

    # Fallback: when community_taxon object is missing, use the main taxon object if IDs match.
    if not comm_taxon_obj:
        taxon_obj = obs.get("taxon") if isinstance(obs.get("taxon"), dict) else None
        taxon_id = _coerce_int((taxon_obj or {}).get("id"))
        if taxon_obj and (comm_taxon_id is None or taxon_id == comm_taxon_id):
            comm_taxon_obj = taxon_obj
            if comm_taxon_id is None:
                comm_taxon_id = taxon_id
        elif detail and isinstance(detail.get("taxon"), dict):
            detail_taxon = detail.get("taxon")
            detail_taxon_id = _coerce_int((detail_taxon or {}).get("id"))
            if comm_taxon_id is None or detail_taxon_id == comm_taxon_id:
                comm_taxon_obj = detail_taxon
                if comm_taxon_id is None:
                    comm_taxon_id = detail_taxon_id

    comm_taxon_id_from_obj, comm_taxon_name, comm_taxon_rank, comm_common_name = _extract_taxon_summary(comm_taxon_obj)
    if comm_taxon_id is None:
        comm_taxon_id = comm_taxon_id_from_obj

    # Current iNaturalist taxon (aggregate/current taxon used by API and site).
    current_taxon_id, current_taxon_name, current_taxon_rank, _ = _extract_taxon_summary(obs.get("taxon"))
    if not current_taxon_name and detail:
        current_taxon_id, current_taxon_name, current_taxon_rank, _ = _extract_taxon_summary(detail.get("taxon"))

    # Final fallback if observer taxon is unavailable.
    if not obs_taxon_name:
        obs_taxon_id, obs_taxon_name, obs_taxon_rank, obs_common_name = (
            current_taxon_id,
            current_taxon_name,
            current_taxon_rank,
            obs_common_name,
        )
    if not obs_taxon_name and detail:
        obs_taxon_id, obs_taxon_name, obs_taxon_rank, obs_common_name = _extract_taxon_summary(detail.get("taxon"))
    if not obs_taxon_name:
        obs_taxon_name = str(obs.get("species_guess") or "").strip() or None

    if obs_common_name is None:
        obs_common_name = comm_common_name

    return {
        "current_taxon_id": current_taxon_id,
        "current_taxon_name": current_taxon_name,
        "current_taxon_rank": current_taxon_rank,
        "observation_taxon_id": obs_taxon_id,
        "observation_taxon_name": obs_taxon_name,
        "observation_taxon_rank": obs_taxon_rank,
        "community_taxon_id": comm_taxon_id,
        "community_taxon_name": comm_taxon_name,
        "community_taxon_rank": comm_taxon_rank,
        "observation_common_name": obs_common_name,
    }


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
    photos = _extract_photo_entries(obs)
    if not photos:
        return None, None, None
    first = photos[0]
    return first.photo_url, first.photo_license_code, first.photo_attribution


def _extract_photo_entries(obs: dict) -> list[InatPhoto]:
    photos = obs.get("photos")
    if not isinstance(photos, list) or not photos:
        return []

    out: list[InatPhoto] = []
    for idx, photo in enumerate(photos, start=1):
        if not isinstance(photo, dict):
            continue
        url = (
            photo.get("large_url")
            or photo.get("medium_url")
            or photo.get("url")
            or (photo.get("sizes") or {}).get("large")
            or (photo.get("sizes") or {}).get("medium")
        )
        if not isinstance(url, str) or not url:
            continue
        url = url.replace("square.", "large.")
        photo_id_raw = photo.get("id")
        photo_id = int(photo_id_raw) if isinstance(photo_id_raw, int) else None
        license_code = photo.get("license_code")
        attribution = photo.get("attribution")
        out.append(
            InatPhoto(
                inat_photo_id=photo_id,
                photo_index=idx,
                photo_url=url,
                photo_license_code=str(license_code) if license_code else None,
                photo_attribution=str(attribution) if attribution else None,
            )
        )
    return out


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


def _suggest_places(client: httpx.Client, base: str, query: str, limit: int = 5) -> list[str]:
    place_query = (query or "").strip()
    if not place_query:
        return []
    try:
        resp = client.get(f"{base}/places/autocomplete", params={"q": place_query, "per_page": max(limit, 5)})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    out: list[str] = []
    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("display_name") or row.get("name") or "").strip()
        if not label:
            continue
        out.append(label)
        if len(out) >= limit:
            break
    return out


def _place_error_message(client: httpx.Client, base: str, place_query: str) -> str:
    suggestions = _suggest_places(client, base, place_query, limit=5)
    if suggestions:
        return (
            "Could not resolve place/location filter. Try one of these iNaturalist place names: "
            + "; ".join(suggestions)
            + "."
        )
    return (
        "Could not resolve place/location filter. Try a broader or clearer iNaturalist place "
        "such as 'Alabama, US' or county format 'Winston County, US, AL'."
    )


def _resolve_project_filter_with_client(
    client: httpx.Client,
    base: str,
    project_id_or_slug: str,
) -> tuple[str, Optional[int], Optional[str]]:
    raw = (project_id_or_slug or "").strip()
    if not raw:
        raise ValueError("Provide an iNaturalist project ID/slug.")

    def normalize_row(row: dict[str, Any]) -> tuple[str, Optional[int], Optional[str]] | None:
        slug = str(row.get("slug") or "").strip()
        title = str(row.get("title") or "").strip() or None
        project_id_raw = row.get("id")
        project_id_num = int(project_id_raw) if isinstance(project_id_raw, int) else None
        canonical = slug or (str(project_id_num) if project_id_num is not None else "")
        if not canonical:
            return None
        return canonical, project_id_num, title

    try:
        resp = client.get(f"{base}/projects/{raw}")
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if results and isinstance(results[0], dict):
            normalized = normalize_row(results[0])
            if normalized:
                return normalized
    except Exception:
        pass

    try:
        resp = client.get(f"{base}/projects/autocomplete", params={"q": raw, "per_page": 30})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        data = {"results": []}

    lower_raw = raw.lower()
    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "").strip().lower()
        row_id = row.get("id")
        if slug == lower_raw or (isinstance(row_id, int) and str(row_id) == raw):
            normalized = normalize_row(row)
            if normalized:
                return normalized

    raise ValueError(
        "Could not find iNaturalist project ID/slug. Use the project's exact slug "
        "(usually lowercase with hyphens) or numeric project ID."
    )


def resolve_project_filter(project_id_or_slug: str) -> tuple[str, Optional[int], Optional[str]]:
    base = settings.inat_base_url.rstrip("/")
    timeout = httpx.Timeout(10.0, connect=5.0)
    headers = {"User-Agent": "myDNAobv/1.0 (+https://mrdbid.com)"}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        return _resolve_project_filter_with_client(client, base, project_id_or_slug)


def estimate_total_observations(
    *,
    inat_user_id: Optional[int],
    inat_username: Optional[str],
    place_query: Optional[str],
    taxon_filter: Optional[str],
    inat_project_id: Optional[str] = None,
) -> dict[str, Any]:
    base = settings.inat_base_url.rstrip("/")
    url = f"{base}/observations"
    timeout = httpx.Timeout(10.0, connect=5.0)
    headers = {"User-Agent": "myDNAobv/1.0 (+https://mrdbid.com)"}

    resolved_user_id: Optional[int] = inat_user_id
    normalized_username = (inat_username or "").strip()
    normalized_project_id = (inat_project_id or "").strip()
    resolved_place_id: Optional[int] = None
    resolved_place_name: Optional[str] = None

    with httpx.Client(timeout=timeout, headers=headers) as client:
        if resolved_user_id is None and not normalized_username and not normalized_project_id:
            raise ValueError("Provide an iNaturalist user ID, username, or project ID/slug.")

        if resolved_user_id is not None:
            detail = _fetch_user_detail_by_id(client, base, int(resolved_user_id))
            if detail:
                canonical_login = str(detail.get("login") or "").strip()
                canonical_id = detail.get("id")
                if canonical_id is not None:
                    resolved_user_id = int(canonical_id)
                if canonical_login:
                    if normalized_username and canonical_login.lower() != normalized_username.lower():
                        raise ValueError(
                            "Provided iNaturalist user ID and username do not match iNaturalist records."
                        )
                    normalized_username = canonical_login
            elif normalized_username:
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
                    normalized_username = row_login
        elif normalized_username:
            user_row = _find_user_by_login(client, base, normalized_username)
            if not user_row:
                raise ValueError("Could not find iNaturalist username.")
            row_id = user_row.get("id")
            row_login = str(user_row.get("login") or "").strip()
            if row_id is not None:
                resolved_user_id = int(row_id)
            if row_login:
                normalized_username = row_login

        if normalized_project_id:
            canonical_project, _, _ = _resolve_project_filter_with_client(client, base, normalized_project_id)
            normalized_project_id = canonical_project

        cleaned_place_query = (place_query or "").strip()
        if cleaned_place_query:
            place_id, place_name = _resolve_place_id(client, base, cleaned_place_query)
            if place_id is None:
                raise ValueError(_place_error_message(client, base, cleaned_place_query))
            resolved_place_id = int(place_id)
            resolved_place_name = place_name or cleaned_place_query

        params: dict[str, Any] = {
            "taxon_id": settings.inat_taxon_id,
            "per_page": 1,
            "page": 1,
            "order_by": "observed_on",
            "order": "desc",
        }
        if resolved_user_id is not None:
            params["user_id"] = resolved_user_id
        elif normalized_username:
            params["user_login"] = normalized_username
        if normalized_project_id:
            params["project_id"] = normalized_project_id
        dna_field_name = _queryable_dna_field_name()
        if dna_field_name:
            params[f"field:{dna_field_name}"] = ""
        cleaned_taxon = (taxon_filter or "").strip()
        if cleaned_taxon:
            params["taxon_name"] = cleaned_taxon
        if resolved_place_id is not None:
            params["place_id"] = resolved_place_id

        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        total = data.get("total_results")

    return {
        "total_results": int(total) if isinstance(total, int) and total >= 0 else 0,
        "resolved_user_id": resolved_user_id,
        "resolved_username": normalized_username or None,
        "resolved_place_id": resolved_place_id,
        "resolved_place_name": resolved_place_name,
    }


def fetch_observations_for_list(obs_list: models.ObservationList) -> Iterable[InatObservation]:
    """
    Placeholder for iNaturalist API integration.

    Intended filters:
    - user_id or user login
    - project_id slug/numeric ID
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
        project_filters = _project_filters_for_list(obs_list)
        resolved_user_id: Optional[int] = obs_list.inat_user_id

        if resolved_user_id is None and not normalized_username and not project_filters:
            raise ValueError("Provide an iNaturalist user ID, username, or project ID/slug.")

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
        elif normalized_username:
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
        if place_query and resolved_place_id is None:
            place_id, place_name = _resolve_place_id(client, base, place_query)
            if place_id is None:
                raise ValueError(_place_error_message(client, base, place_query))
            resolved_place_id = int(place_id)
            obs_list.inat_place_id = resolved_place_id
            if place_name:
                obs_list.place_query = place_query

        seen_inat_ids: set[int] = set()
        project_cycle: list[Optional[str]] = project_filters if project_filters else [None]

        for project_filter in project_cycle:
            page = 1
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
                if project_filter:
                    params["project_id"] = project_filter
                dna_field_name = _queryable_dna_field_name()
                if dna_field_name:
                    # iNaturalist search URL syntax supports field filters.
                    params[f"field:{dna_field_name}"] = ""
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

                    inat_id = _coerce_int(obs.get("id"))
                    if inat_id is None:
                        continue
                    if inat_id in seen_inat_ids:
                        continue

                    field_value = _extract_field_value(obs, obs_list.inat_dna_field_id)
                    detail: Optional[dict] = None
                    if field_value is None:
                        detail = _fetch_observation_detail(client, base, inat_id)
                        if detail:
                            field_value = _extract_field_value(detail, obs_list.inat_dna_field_id)
                    if field_value is None:
                        continue

                    species_guess = obs.get("species_guess")
                    user = obs.get("user") or {}
                    user_name = user.get("name") or user.get("login")
                    inat_url = obs.get("uri") or obs.get("url") or f"https://www.inaturalist.org/observations/{inat_id}"
                    observed_at = _parse_observed_at(obs)
                    photo_entries = _extract_photo_entries(obs)
                    declared_photo_count = 0
                    for count_key in ("photos_count", "observation_photos_count"):
                        raw_count = obs.get(count_key)
                        if isinstance(raw_count, int) and raw_count > 0:
                            declared_photo_count = raw_count
                            break
                    if settings.export_include_all_photos:
                        max_needed_photos = max(1, min(settings.export_max_photos_per_observation, 8))
                        needs_more_for_export = len(photo_entries) < max_needed_photos
                    else:
                        needs_more_for_export = False
                    if settings.export_include_all_photos and needs_more_for_export and declared_photo_count > len(photo_entries):
                        if detail is None:
                            detail = _fetch_observation_detail(client, base, inat_id)
                        if detail:
                            detail_entries = _extract_photo_entries(detail)
                            if detail_entries:
                                photo_entries = detail_entries
                    if not photo_entries and detail is None:
                        detail = _fetch_observation_detail(client, base, inat_id)
                        if detail:
                            photo_entries = _extract_photo_entries(detail)

                    taxa = _extract_taxa(obs, detail=detail)
                    observation_taxon_name = taxa["observation_taxon_name"]
                    observation_common_name = taxa["observation_common_name"]

                    # Keep legacy fields populated for backwards compatibility with existing templates/export code.
                    scientific_name = (
                        str(observation_taxon_name).strip()
                        if observation_taxon_name
                        else (str(obs.get("scientific_name") or "").strip() or None)
                    )
                    taxon_name = (
                        str(taxa["current_taxon_name"]).strip()
                        if taxa["current_taxon_name"]
                        else (str(obs.get("taxon_name") or "").strip() or None)
                    )
                    common_name = (
                        str(observation_common_name).strip()
                        if observation_common_name
                        else None
                    )
                    if common_name is None:
                        taxon_obj = obs.get("taxon") or {}
                        common_name = (
                            str(taxon_obj.get("preferred_common_name") or taxon_obj.get("common_name") or "").strip()
                            or None
                        )

                    if photo_entries:
                        photo_url = photo_entries[0].photo_url
                        photo_license_code = photo_entries[0].photo_license_code
                        photo_attribution = photo_entries[0].photo_attribution
                    else:
                        photo_url = None
                        photo_license_code = None
                        photo_attribution = None

                    if not matches_taxon(taxon_name, species_guess, scientific_name):
                        continue

                    seen_inat_ids.add(inat_id)
                    yield InatObservation(
                        inat_id=inat_id,
                        taxon_name=taxon_name,
                        species_guess=species_guess,
                        scientific_name=scientific_name,
                        common_name=common_name,
                        observation_taxon_id=taxa["observation_taxon_id"],
                        observation_taxon_name=taxa["observation_taxon_name"],
                        observation_taxon_rank=taxa["observation_taxon_rank"],
                        community_taxon_id=taxa["community_taxon_id"],
                        community_taxon_name=taxa["community_taxon_name"],
                        community_taxon_rank=taxa["community_taxon_rank"],
                        user_name=user_name,
                        observed_at=observed_at,
                        inat_url=inat_url,
                        dna_field_value=field_value,
                        photo_url=photo_url,
                        photo_license_code=photo_license_code,
                        photo_attribution=photo_attribution,
                        photo_entries=photo_entries,
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
