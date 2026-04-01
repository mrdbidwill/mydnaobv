#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/deploy_alert_utils.sh"

SMOKE_BASE_URL="${SMOKE_BASE_URL:-http://127.0.0.1}"
SMOKE_HOST_HEADER="${SMOKE_HOST_HEADER:-}"
SMOKE_PATHS="${SMOKE_PATHS:-}"
SMOKE_MAX_PUBLIC_LINKS="${SMOKE_MAX_PUBLIC_LINKS:-3}"
POST_DEPLOY_ALERT_WEBHOOK_URL="${POST_DEPLOY_ALERT_WEBHOOK_URL:-}"
DEPLOY_ALERT_FORMAT="${DEPLOY_ALERT_FORMAT:-plain}"
DEPLOY_ALERT_NTFY_BASE_URL="${DEPLOY_ALERT_NTFY_BASE_URL:-https://ntfy.sh}"
SMOKE_SUPPRESS_ALERTS="${SMOKE_SUPPRESS_ALERTS:-0}"
APP_COMMIT="${APP_COMMIT:-unknown}"
APP_SERVICE="${APP_SERVICE:-mydnaobv}"

log() {
  printf '[post-deploy-smoke] %s\n' "$*"
}

normalize_smoke_alert_url() {
  local normalized=""
  local reason=""
  if deploy_alert_validate_url "${POST_DEPLOY_ALERT_WEBHOOK_URL}" normalized reason "${DEPLOY_ALERT_FORMAT}" "${DEPLOY_ALERT_NTFY_BASE_URL}"; then
    POST_DEPLOY_ALERT_WEBHOOK_URL="${normalized}"
    return 0
  fi
  local rc=$?
  if [[ "${rc}" -eq 1 ]]; then
    POST_DEPLOY_ALERT_WEBHOOK_URL=""
    return 0
  fi
  log "Ignoring invalid smoke alert webhook URL (${reason})."
  POST_DEPLOY_ALERT_WEBHOOK_URL=""
}

send_alert() {
  local message="$1"
  if [[ "${SMOKE_SUPPRESS_ALERTS}" == "1" ]]; then
    return 0
  fi
  if [[ -z "${POST_DEPLOY_ALERT_WEBHOOK_URL}" ]]; then
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    log "curl not found; cannot send webhook alert."
    return 0
  fi
  if ! curl --silent --show-error --fail --location --max-time 10 --retry 2 --retry-delay 1 \
    --request POST \
    -H "Content-Type: text/plain; charset=utf-8" \
    --data-binary "${message}" \
    --url "${POST_DEPLOY_ALERT_WEBHOOK_URL}" >/dev/null 2>&1; then
    log "Failed to send webhook alert."
  fi
}

request_headers() {
  local url="$1"
  local headers_file="$2"
  if [[ -n "${SMOKE_HOST_HEADER}" ]]; then
    curl --silent --show-error --max-time 30 -D "${headers_file}" -o /dev/null \
      -H "Host: ${SMOKE_HOST_HEADER}" \
      "${url}"
  else
    curl --silent --show-error --max-time 30 -D "${headers_file}" -o /dev/null "${url}"
  fi
}

request_body() {
  local url="$1"
  local body_file="$2"
  if [[ -n "${SMOKE_HOST_HEADER}" ]]; then
    curl --location --fail --silent --show-error --max-time 30 \
      -H "Host: ${SMOKE_HOST_HEADER}" \
      "${url}" >"${body_file}"
  else
    curl --location --fail --silent --show-error --max-time 30 "${url}" >"${body_file}"
  fi
}

check_public_download_path() {
  local path="$1"
  local url="${SMOKE_BASE_URL%/}${path}"
  local headers_file
  headers_file="$(mktemp)"
  if ! request_headers "${url}" "${headers_file}"; then
    rm -f "${headers_file}"
    return 1
  fi

  local status_code
  status_code="$(awk 'toupper($1) ~ /^HTTP/ {code=$2} END {print code}' "${headers_file}")"
  if [[ "${status_code}" == "200" ]]; then
    rm -f "${headers_file}"
    return 0
  fi

  if [[ "${status_code}" =~ ^30[12378]$ ]]; then
    local location
    location="$(awk 'tolower($1)=="location:" {print $2}' "${headers_file}" | tr -d '\r' | tail -n1)"
    rm -f "${headers_file}"
    if [[ -z "${location}" ]]; then
      return 1
    fi
    curl --location --fail --silent --show-error --max-time 45 "${location}" -o /dev/null
    return $?
  fi

  rm -f "${headers_file}"
  return 1
}

main() {
  normalize_smoke_alert_url

  local -a paths=()
  if [[ -n "${SMOKE_PATHS}" ]]; then
    IFS=',' read -r -a paths <<< "${SMOKE_PATHS}"
  else
    local body_file
    body_file="$(mktemp)"
    request_body "${SMOKE_BASE_URL%/}/" "${body_file}"
    while IFS= read -r discovered_path; do
      paths+=("${discovered_path}")
    done < <(
      grep -Eo '/public/lists/[0-9]+/artifacts/[0-9]+/download' "${body_file}" \
      | awk '!seen[$0]++' \
      | head -n "${SMOKE_MAX_PUBLIC_LINKS}"
    )
    rm -f "${body_file}"
  fi

  if [[ "${#paths[@]}" -eq 0 ]]; then
    local msg="post-deploy smoke failed (${APP_SERVICE}@${APP_COMMIT}): no public artifact links found."
    log "${msg}"
    send_alert "${msg}"
    exit 1
  fi

  local -a failures=()
  local path=""
  for path in "${paths[@]}"; do
    if [[ -z "${path}" ]]; then
      continue
    fi
    if check_public_download_path "${path}"; then
      log "OK ${path}"
    else
      failures+=("${path}")
      log "FAIL ${path}"
    fi
  done

  if [[ "${#failures[@]}" -gt 0 ]]; then
    local msg
    msg="post-deploy smoke failed (${APP_SERVICE}@${APP_COMMIT}) paths: ${failures[*]}"
    log "${msg}"
    send_alert "${msg}"
    exit 1
  fi

  log "Post-deploy smoke checks passed."
}

main "$@"
