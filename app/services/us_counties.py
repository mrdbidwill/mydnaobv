from __future__ import annotations

from dataclasses import dataclass

import httpx

CENSUS_COUNTY_ENDPOINT = "https://api.census.gov/data/2020/dec/pl"

# (state_code, state_name, state_fips)
US_STATE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("AL", "Alabama", "01"),
    ("AK", "Alaska", "02"),
    ("AZ", "Arizona", "04"),
    ("AR", "Arkansas", "05"),
    ("CA", "California", "06"),
    ("CO", "Colorado", "08"),
    ("CT", "Connecticut", "09"),
    ("DE", "Delaware", "10"),
    ("DC", "District of Columbia", "11"),
    ("FL", "Florida", "12"),
    ("GA", "Georgia", "13"),
    ("HI", "Hawaii", "15"),
    ("ID", "Idaho", "16"),
    ("IL", "Illinois", "17"),
    ("IN", "Indiana", "18"),
    ("IA", "Iowa", "19"),
    ("KS", "Kansas", "20"),
    ("KY", "Kentucky", "21"),
    ("LA", "Louisiana", "22"),
    ("ME", "Maine", "23"),
    ("MD", "Maryland", "24"),
    ("MA", "Massachusetts", "25"),
    ("MI", "Michigan", "26"),
    ("MN", "Minnesota", "27"),
    ("MS", "Mississippi", "28"),
    ("MO", "Missouri", "29"),
    ("MT", "Montana", "30"),
    ("NE", "Nebraska", "31"),
    ("NV", "Nevada", "32"),
    ("NH", "New Hampshire", "33"),
    ("NJ", "New Jersey", "34"),
    ("NM", "New Mexico", "35"),
    ("NY", "New York", "36"),
    ("NC", "North Carolina", "37"),
    ("ND", "North Dakota", "38"),
    ("OH", "Ohio", "39"),
    ("OK", "Oklahoma", "40"),
    ("OR", "Oregon", "41"),
    ("PA", "Pennsylvania", "42"),
    ("RI", "Rhode Island", "44"),
    ("SC", "South Carolina", "45"),
    ("SD", "South Dakota", "46"),
    ("TN", "Tennessee", "47"),
    ("TX", "Texas", "48"),
    ("UT", "Utah", "49"),
    ("VT", "Vermont", "50"),
    ("VA", "Virginia", "51"),
    ("WA", "Washington", "53"),
    ("WV", "West Virginia", "54"),
    ("WI", "Wisconsin", "55"),
    ("WY", "Wyoming", "56"),
)

STATE_OPTIONS: tuple[tuple[str, str], ...] = tuple((code, name) for code, name, _ in US_STATE_ROWS)
STATE_FIPS_BY_CODE: dict[str, str] = {code: fips for code, _name, fips in US_STATE_ROWS}


@dataclass(frozen=True)
class CountySeedRow:
    county_name: str
    place_query: str


def normalize_state_code(raw_state_code: str) -> str | None:
    candidate = (raw_state_code or "").strip().upper()
    if candidate in STATE_FIPS_BY_CODE:
        return candidate
    return None


def fetch_counties_for_state(state_code: str) -> list[CountySeedRow]:
    normalized_state = normalize_state_code(state_code)
    if not normalized_state:
        raise ValueError("Please select a valid US state.")

    params = {
        "get": "NAME",
        "for": "county:*",
        "in": f"state:{STATE_FIPS_BY_CODE[normalized_state]}",
    }
    headers = {"User-Agent": "myDNAobv/1.0 (+https://mrdbid.com)"}
    with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), headers=headers) as client:
        response = client.get(CENSUS_COUNTY_ENDPOINT, params=params)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("County list response was empty for the selected state.")

    rows: list[CountySeedRow] = []
    seen: set[str] = set()
    for raw_row in payload[1:]:
        if not isinstance(raw_row, list) or not raw_row:
            continue
        raw_name = str(raw_row[0] or "").strip()
        county_name = raw_name.split(",", 1)[0].strip()
        if not county_name:
            continue

        place_query = f"{county_name}, US, {normalized_state}"
        if place_query in seen:
            continue
        seen.add(place_query)
        rows.append(CountySeedRow(county_name=county_name, place_query=place_query))

    if not rows:
        raise ValueError("No counties were returned for the selected state.")
    return rows
