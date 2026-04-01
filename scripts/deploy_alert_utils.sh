#!/usr/bin/env bash

# Shared deploy alert helpers for URL normalization/validation.
# Return codes for deploy_alert_validate_url:
#   0 = valid URL (written to output variable)
#   1 = empty/unspecified value
#   2 = invalid value

DEPLOY_ALERT_VALIDATE_REASON=""
DEPLOY_ALERT_VALIDATED_URL=""

deploy_alert_validate_url() {
  local raw="${1:-}"
  local out_var="${2:-DEPLOY_ALERT_VALIDATED_URL}"
  local reason_var="${3:-DEPLOY_ALERT_VALIDATE_REASON}"
  local alert_format="${4:-plain}"
  local ntfy_base_url="${5:-https://ntfy.sh}"
  local normalized="${raw//$'\r'/}"
  local fmt

  normalized="$(printf '%s' "${normalized}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fmt="$(printf '%s' "${alert_format}" | tr '[:upper:]' '[:lower:]')"
  ntfy_base_url="${ntfy_base_url%/}"

  printf -v "${out_var}" '%s' ""
  printf -v "${reason_var}" '%s' ""
  if [[ -z "${normalized}" ]]; then
    printf -v "${reason_var}" '%s' "empty"
    return 1
  fi
  if [[ "${normalized}" == -* ]]; then
    printf -v "${reason_var}" '%s' "starts_with_dash"
    return 2
  fi
  if [[ "${normalized}" =~ [[:space:]] ]]; then
    printf -v "${reason_var}" '%s' "contains_whitespace"
    return 2
  fi
  if [[ "${normalized}" =~ ^https?:// ]]; then
    printf -v "${out_var}" '%s' "${normalized}"
    return 0
  fi
  if [[ "${fmt}" == "ntfy" && "${normalized}" =~ ^[A-Za-z0-9._~-]+$ && "${ntfy_base_url}" =~ ^https?:// ]]; then
    printf -v "${out_var}" '%s' "${ntfy_base_url}/${normalized}"
    return 0
  fi

  printf -v "${reason_var}" '%s' "invalid_scheme"
  return 2
}
