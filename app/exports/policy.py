from __future__ import annotations

from dataclasses import dataclass

from app.exports.config import export_config

_DENY_VALUES = {
    "",
    "none",
    "null",
    "copyright",
    "all rights reserved",
    "arr",
}


@dataclass(frozen=True)
class LicenseDecision:
    allowed: bool
    normalized_license: str
    reason: str


def normalize_license_code(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().lower()


def evaluate_license(value: str | None) -> LicenseDecision:
    normalized = normalize_license_code(value)
    if not normalized:
        if export_config.allow_unlicensed:
            return LicenseDecision(True, normalized, "missing_license_allowed_by_config")
        return LicenseDecision(False, normalized, "missing_license")
    if normalized in _DENY_VALUES:
        return LicenseDecision(False, normalized, "explicitly_restricted")
    if normalized in export_config.allowed_licenses:
        return LicenseDecision(True, normalized, "allowed")
    return LicenseDecision(False, normalized, "not_in_allowlist")


def build_attribution_line(
    *,
    observation_id: int,
    observation_url: str,
    attribution_text: str | None,
    license_code: str | None,
) -> str:
    attribution = (attribution_text or "Unknown author").strip()
    license_value = normalize_license_code(license_code) or "license unknown"
    return (
        f"Observation {observation_id}: {attribution}. "
        f"License: {license_value}. Source: {observation_url}"
    )
