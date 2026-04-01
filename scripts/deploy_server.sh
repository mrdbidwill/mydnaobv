#!/usr/bin/env bash
set -Eeuo pipefail

# Deploy myDNAobv on the server host.
# Intended default layout from runbooks:
# - repo path: /opt/mydnaobv/app
# - service: mydnaobv

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/deploy_alert_utils.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/deploy_env_override_utils.sh"

DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-$HOME/.config/mydnaobv/deploy.env}"
deploy_env_capture_overrides \
  APP_DIR \
  BRANCH \
  VENV_DIR \
  SERVICE_NAME \
  HEALTHCHECK_URL \
  HEALTHCHECK_HOST_HEADER \
  HEALTHCHECK_ATTEMPTS \
  HEALTHCHECK_RETRY_DELAY_SECONDS \
  ALLOW_DIRTY \
  ALLOW_UNTRACKED \
  RUN_TESTS \
  SYSTEMCTL_USE_SUDO \
  GIT_ATTEMPTS \
  GIT_RETRY_DELAY_SECONDS \
  PIP_ATTEMPTS \
  PIP_RETRY_DELAY_SECONDS \
  RUN_POST_DEPLOY_SMOKE \
  SMOKE_BASE_URL \
  SMOKE_HOST_HEADER \
  SMOKE_PATHS \
  SMOKE_MAX_PUBLIC_LINKS \
  POST_DEPLOY_ALERT_WEBHOOK_URL \
  POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL \
  DEPLOY_ALERT_FORMAT \
  DEPLOY_ALERT_NTFY_BASE_URL \
  DEPLOY_ALERT_TIMEOUT_SECONDS \
  DEPLOY_ALERT_ON_SUCCESS \
  DEPLOY_ALERT_MAX_CHARS \
  ENABLE_AUTO_ROLLBACK \
  ROLLBACK_RUN_SMOKE \
  ROLLBACK_SMOKE_PATHS \
  RUN_MIGRATION_COMPAT_CHECK \
  ALLOW_BREAKING_MIGRATIONS

if [[ -f "${DEPLOY_ENV_FILE}" ]]; then
  mode="$(stat -c '%a' "${DEPLOY_ENV_FILE}" 2>/dev/null || stat -f '%Lp' "${DEPLOY_ENV_FILE}" 2>/dev/null || true)"
  if [[ -n "${mode}" && "${mode}" != "600" ]]; then
    printf '[deploy] Warning: expected 600 permissions on %s (got %s)\n' "${DEPLOY_ENV_FILE}" "${mode}" >&2
  fi
  # shellcheck source=/dev/null
  source "${DEPLOY_ENV_FILE}"
fi
deploy_env_restore_overrides
log "Deploy env file status: path=${DEPLOY_ENV_FILE} exists=$([[ -f "${DEPLOY_ENV_FILE}" ]] && echo 1 || echo 0)"

APP_DIR="${APP_DIR:-/opt/mydnaobv/app}"
BRANCH="${BRANCH:-main}"
VENV_DIR="${VENV_DIR:-.venv}"
SERVICE_NAME="${SERVICE_NAME:-mydnaobv}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1/}"
HEALTHCHECK_HOST_HEADER="${HEALTHCHECK_HOST_HEADER:-}"
HEALTHCHECK_ATTEMPTS="${HEALTHCHECK_ATTEMPTS:-6}"
HEALTHCHECK_RETRY_DELAY_SECONDS="${HEALTHCHECK_RETRY_DELAY_SECONDS:-5}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
ALLOW_UNTRACKED="${ALLOW_UNTRACKED:-1}"
RUN_TESTS="${RUN_TESTS:-0}"
SYSTEMCTL_USE_SUDO="${SYSTEMCTL_USE_SUDO:-1}"
GIT_ATTEMPTS="${GIT_ATTEMPTS:-3}"
GIT_RETRY_DELAY_SECONDS="${GIT_RETRY_DELAY_SECONDS:-3}"
PIP_ATTEMPTS="${PIP_ATTEMPTS:-3}"
PIP_RETRY_DELAY_SECONDS="${PIP_RETRY_DELAY_SECONDS:-4}"
RUN_POST_DEPLOY_SMOKE="${RUN_POST_DEPLOY_SMOKE:-1}"
SMOKE_BASE_URL="${SMOKE_BASE_URL:-http://127.0.0.1}"
SMOKE_HOST_HEADER="${SMOKE_HOST_HEADER:-${HEALTHCHECK_HOST_HEADER}}"
SMOKE_PATHS="${SMOKE_PATHS:-}"
SMOKE_MAX_PUBLIC_LINKS="${SMOKE_MAX_PUBLIC_LINKS:-3}"
POST_DEPLOY_ALERT_WEBHOOK_URL="${POST_DEPLOY_ALERT_WEBHOOK_URL:-}"
POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL="${POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL:-}"
DEPLOY_ALERT_FORMAT="${DEPLOY_ALERT_FORMAT:-plain}"
DEPLOY_ALERT_NTFY_BASE_URL="${DEPLOY_ALERT_NTFY_BASE_URL:-https://ntfy.sh}"
DEPLOY_ALERT_TIMEOUT_SECONDS="${DEPLOY_ALERT_TIMEOUT_SECONDS:-10}"
DEPLOY_ALERT_ON_SUCCESS="${DEPLOY_ALERT_ON_SUCCESS:-0}"
DEPLOY_ALERT_MAX_CHARS="${DEPLOY_ALERT_MAX_CHARS:-1800}"
ENABLE_AUTO_ROLLBACK="${ENABLE_AUTO_ROLLBACK:-1}"
ROLLBACK_RUN_SMOKE="${ROLLBACK_RUN_SMOKE:-1}"
ROLLBACK_SMOKE_PATHS="${ROLLBACK_SMOKE_PATHS:-}"
RUN_MIGRATION_COMPAT_CHECK="${RUN_MIGRATION_COMPAT_CHECK:-1}"
ALLOW_BREAKING_MIGRATIONS="${ALLOW_BREAKING_MIGRATIONS:-0}"
log "Deploy alert raw lengths before normalize: primary=${#POST_DEPLOY_ALERT_WEBHOOK_URL}, fallback=${#POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL}, format=${DEPLOY_ALERT_FORMAT}"

DEPLOY_PHASE="init"
PRE_DEPLOY_COMMIT=""
PRE_DEPLOY_SHORT=""
IN_ERROR_HANDLER=0

log() {
  printf '[deploy] %s\n' "$*"
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

normalize_alert_url() {
  local label="$1"
  local raw="$2"
  local normalized=""
  local reason=""
  if deploy_alert_validate_url "${raw}" normalized reason "${DEPLOY_ALERT_FORMAT}" "${DEPLOY_ALERT_NTFY_BASE_URL}"; then
    printf '%s' "${normalized}"
    return 0
  fi

  local rc=$?
  if [[ "${rc}" -eq 1 ]]; then
    printf ''
    return 0
  fi

  log "Ignoring invalid ${label} deploy alert webhook URL (${reason})." >&2
  printf ''
}

send_alert_to_webhook() {
  local label="${1:-endpoint}"
  local url="$2"
  local message="$3"
  local body="${message}"
  local max_chars="${DEPLOY_ALERT_MAX_CHARS}"
  if [[ "${max_chars}" =~ ^[0-9]+$ ]] && (( max_chars > 32 )) && (( ${#body} > max_chars )); then
    body="${body:0:max_chars} ... [truncated]"
  fi

  local err_file
  err_file="$(mktemp)"
  local http_code=""
  local curl_rc=0

  local fmt
  fmt="$(printf '%s' "${DEPLOY_ALERT_FORMAT}" | tr '[:upper:]' '[:lower:]')"
  case "${fmt}" in
    slack)
      local payload_slack
      payload_slack="{\"text\":\"$(json_escape "${body}")\"}"
      http_code="$(curl --silent --show-error --location --max-time "${DEPLOY_ALERT_TIMEOUT_SECONDS}" --retry 1 --retry-delay 1 \
        --request POST \
        -H "Content-Type: application/json" \
        --data "${payload_slack}" \
        --url "${url}" \
        --output /dev/null \
        --write-out '%{http_code}' 2>"${err_file}")" || curl_rc=$?
      ;;
    discord)
      local payload_discord
      payload_discord="{\"content\":\"$(json_escape "${body}")\"}"
      http_code="$(curl --silent --show-error --location --max-time "${DEPLOY_ALERT_TIMEOUT_SECONDS}" --retry 1 --retry-delay 1 \
        --request POST \
        -H "Content-Type: application/json" \
        --data "${payload_discord}" \
        --url "${url}" \
        --output /dev/null \
        --write-out '%{http_code}' 2>"${err_file}")" || curl_rc=$?
      ;;
    plain|ntfy|*)
      http_code="$(curl --silent --show-error --location --max-time "${DEPLOY_ALERT_TIMEOUT_SECONDS}" --retry 1 --retry-delay 1 \
        --request POST \
        -H "Content-Type: text/plain; charset=utf-8" \
        --data-binary "${body}" \
        --url "${url}" \
        --output /dev/null \
        --write-out '%{http_code}' 2>"${err_file}")" || curl_rc=$?
      ;;
  esac

  if [[ "${curl_rc}" -ne 0 ]]; then
    local err_msg=""
    err_msg="$(tr '\n' ' ' < "${err_file}" | sed -e 's/[[:space:]]\\+/ /g' | cut -c1-200)"
    rm -f "${err_file}"
    log "Deploy alert ${label} failed (curl_exit=${curl_rc}, http_code=${http_code:-000}, err=${err_msg:-none})."
    return 1
  fi

  rm -f "${err_file}"
  if [[ ! "${http_code}" =~ ^2[0-9][0-9]$ ]]; then
    log "Deploy alert ${label} failed (http_code=${http_code})."
    return 1
  fi

  return 0
}

notify_alert() {
  local message="$1"
  local sent=0
  local attempted=0
  if [[ -n "${POST_DEPLOY_ALERT_WEBHOOK_URL}" ]]; then
    attempted=1
    if send_alert_to_webhook "primary" "${POST_DEPLOY_ALERT_WEBHOOK_URL}" "${message}"; then
      sent=1
    fi
  fi
  if [[ -n "${POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL}" ]]; then
    attempted=1
    if send_alert_to_webhook "fallback" "${POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL}" "${message}"; then
      sent=1
    fi
  fi

  if command -v logger >/dev/null 2>&1; then
    logger -t "mydnaobv-deploy" -- "${message}" || true
  fi

  if [[ "${attempted}" != "1" ]]; then
    log "No valid deploy alert webhook configured; alert also written to syslog."
  elif [[ "${sent}" != "1" ]]; then
    log "Failed to deliver deploy alert to configured webhook endpoints."
  fi
}

POST_DEPLOY_ALERT_WEBHOOK_URL="$(normalize_alert_url "primary" "${POST_DEPLOY_ALERT_WEBHOOK_URL}")"
POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL="$(normalize_alert_url "fallback" "${POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL}")"
log "Deploy alert endpoints configured: primary=$([[ -n "${POST_DEPLOY_ALERT_WEBHOOK_URL}" ]] && echo 1 || echo 0), fallback=$([[ -n "${POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL}" ]] && echo 1 || echo 0), format=${DEPLOY_ALERT_FORMAT}"

abort_deploy() {
  local message="$1"
  log "${message}"
  notify_alert "${message}"
  exit 1
}

run_systemctl() {
  if [[ "${SYSTEMCTL_USE_SUDO}" == "1" ]]; then
    if ! sudo -n systemctl "$@"; then
      log "systemctl via sudo failed for command: systemctl $*"
      log "Ensure deploy user has NOPASSWD sudo for required service commands."
      return 1
    fi
  else
    systemctl "$@"
  fi
}

run_with_retry() {
  local attempts="$1"
  local delay_seconds="$2"
  shift 2

  local try=1
  while true; do
    if "$@"; then
      return 0
    fi
    if [[ "${try}" -ge "${attempts}" ]]; then
      return 1
    fi
    log "Retry ${try}/${attempts} failed for: $*"
    sleep "${delay_seconds}"
    try=$((try + 1))
  done
}

run_health_check() {
  if ! command -v curl >/dev/null 2>&1; then
    log "curl not found; skipping health check"
    return 0
  fi

  log "Health check ${HEALTHCHECK_URL}"
  local health_ok=0
  local attempt=0
  for attempt in $(seq 1 "${HEALTHCHECK_ATTEMPTS}"); do
    if [[ -n "${HEALTHCHECK_HOST_HEADER}" ]]; then
      if curl --location --fail --silent --show-error --max-time 20 \
        -H "Host: ${HEALTHCHECK_HOST_HEADER}" \
        "${HEALTHCHECK_URL}" >/dev/null; then
        health_ok=1
        break
      fi
    else
      if curl --location --fail --silent --show-error --max-time 20 "${HEALTHCHECK_URL}" >/dev/null; then
        health_ok=1
        break
      fi
    fi

    if [[ "${attempt}" -lt "${HEALTHCHECK_ATTEMPTS}" ]]; then
      log "Health check attempt ${attempt}/${HEALTHCHECK_ATTEMPTS} failed; retrying in ${HEALTHCHECK_RETRY_DELAY_SECONDS}s."
      sleep "${HEALTHCHECK_RETRY_DELAY_SECONDS}"
    fi
  done

  if [[ "${health_ok}" != "1" ]]; then
    log "Health check failed after ${HEALTHCHECK_ATTEMPTS} attempts."
    return 1
  fi
  return 0
}

run_post_deploy_smoke() {
  local commit_short="$1"
  local force_run="${2:-0}"
  local smoke_paths_override="${3-__USE_DEFAULT__}"
  if [[ "${smoke_paths_override}" == "__USE_DEFAULT__" ]]; then
    smoke_paths_override="${SMOKE_PATHS}"
  fi
  if [[ "${RUN_POST_DEPLOY_SMOKE}" != "1" && "${force_run}" != "1" ]]; then
    return 0
  fi
  if [[ ! -x "./scripts/post_deploy_smoke.sh" ]]; then
    log "post deploy smoke script not executable; skipping."
    return 0
  fi

  log "Running post-deploy smoke checks"
  APP_COMMIT="${commit_short}" \
    APP_SERVICE="${SERVICE_NAME}" \
    SMOKE_BASE_URL="${SMOKE_BASE_URL}" \
    SMOKE_HOST_HEADER="${SMOKE_HOST_HEADER}" \
    SMOKE_PATHS="${smoke_paths_override}" \
    SMOKE_MAX_PUBLIC_LINKS="${SMOKE_MAX_PUBLIC_LINKS}" \
    POST_DEPLOY_ALERT_WEBHOOK_URL="${POST_DEPLOY_ALERT_WEBHOOK_URL}" \
    DEPLOY_ALERT_FORMAT="${DEPLOY_ALERT_FORMAT}" \
    DEPLOY_ALERT_NTFY_BASE_URL="${DEPLOY_ALERT_NTFY_BASE_URL}" \
    SMOKE_SUPPRESS_ALERTS="1" \
    ./scripts/post_deploy_smoke.sh
}

perform_rollback() {
  if [[ "${ENABLE_AUTO_ROLLBACK}" != "1" ]]; then
    log "Auto-rollback disabled."
    return 1
  fi
  if [[ -z "${PRE_DEPLOY_COMMIT}" ]]; then
    log "Cannot rollback: previous commit not recorded."
    return 1
  fi

  DEPLOY_PHASE="rollback_checkout"
  log "Rolling back to ${PRE_DEPLOY_SHORT}"
  if [[ "$(git rev-parse HEAD)" != "${PRE_DEPLOY_COMMIT}" ]]; then
    if ! git reset --hard "${PRE_DEPLOY_COMMIT}"; then
      return 1
    fi
  fi

  DEPLOY_PHASE="rollback_venv"
  if [[ ! -d "${VENV_DIR}" ]]; then
    if ! python3 -m venv "${VENV_DIR}"; then
      return 1
    fi
  fi
  # shellcheck source=/dev/null
  if ! source "${VENV_DIR}/bin/activate"; then
    return 1
  fi

  DEPLOY_PHASE="rollback_dependencies"
  if ! run_with_retry "${PIP_ATTEMPTS}" "${PIP_RETRY_DELAY_SECONDS}" \
    pip install --disable-pip-version-check -r requirements.txt; then
    return 1
  fi

  if command -v systemctl >/dev/null 2>&1; then
    DEPLOY_PHASE="rollback_restart"
    if ! run_systemctl restart "${SERVICE_NAME}"; then
      return 1
    fi
    if ! run_systemctl is-active --quiet "${SERVICE_NAME}"; then
      return 1
    fi
    log "Rollback service restart complete: ${SERVICE_NAME}"
  fi

  DEPLOY_PHASE="rollback_health"
  if ! run_health_check; then
    return 1
  fi

  if [[ "${ROLLBACK_RUN_SMOKE}" == "1" ]]; then
    DEPLOY_PHASE="rollback_smoke"
    if ! run_post_deploy_smoke "${PRE_DEPLOY_SHORT}" "1" "${ROLLBACK_SMOKE_PATHS}"; then
      return 1
    fi
  fi

  return 0
}

on_error() {
  local exit_code="$1"
  local line_no="$2"
  local failing_command="$3"

  if [[ "${IN_ERROR_HANDLER}" == "1" ]]; then
    exit "${exit_code}"
  fi
  IN_ERROR_HANDLER=1
  set +e

  local current_short
  current_short="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  local fail_message
  fail_message="Deploy failed on ${SERVICE_NAME} at phase=${DEPLOY_PHASE}, commit=${current_short}, line=${line_no}, exit=${exit_code}, cmd=${failing_command}"
  log "${fail_message}"
  notify_alert "${fail_message}"

  if [[ "${ENABLE_AUTO_ROLLBACK}" == "1" ]]; then
    if perform_rollback; then
      local rollback_ok
      rollback_ok="Rollback succeeded to ${PRE_DEPLOY_SHORT}. Note: DB migrations are not auto-reverted; keep migrations backward-compatible."
      log "${rollback_ok}"
      notify_alert "${rollback_ok}"
    else
      local rollback_fail
      rollback_fail="CRITICAL: rollback failed for ${SERVICE_NAME}. Manual intervention required immediately."
      log "${rollback_fail}"
      notify_alert "${rollback_fail}"
    fi
  else
    local rollback_disabled
    rollback_disabled="Auto-rollback disabled for ${SERVICE_NAME}. Manual intervention required."
    log "${rollback_disabled}"
    notify_alert "${rollback_disabled}"
  fi

  exit "${exit_code}"
}

trap 'on_error $? $LINENO "$BASH_COMMAND"' ERR

if [[ ! -d "${APP_DIR}" ]]; then
  abort_deploy "APP_DIR does not exist: ${APP_DIR}"
fi

cd "${APP_DIR}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  abort_deploy "APP_DIR is not a git repository: ${APP_DIR}"
fi

PRE_DEPLOY_COMMIT="$(git rev-parse HEAD)"
PRE_DEPLOY_SHORT="$(git rev-parse --short HEAD)"

if [[ "${ALLOW_DIRTY}" != "1" ]]; then
  if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
    msg_dirty="Repository has tracked uncommitted changes; aborting. Set ALLOW_DIRTY=1 to bypass."
    log "${msg_dirty}"
    git status --short
    abort_deploy "${msg_dirty}"
  fi

  if [[ "${ALLOW_UNTRACKED}" != "1" ]] && [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    msg_untracked="Repository has untracked files; aborting. Set ALLOW_UNTRACKED=1 to bypass."
    log "${msg_untracked}"
    git status --short
    abort_deploy "${msg_untracked}"
  fi
fi

DEPLOY_PHASE="git_fetch"
log "Fetching latest ${BRANCH}"
run_with_retry "${GIT_ATTEMPTS}" "${GIT_RETRY_DELAY_SECONDS}" \
  git fetch origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"
DEPLOY_PHASE="git_checkout"
git checkout "${BRANCH}"
DEPLOY_PHASE="git_merge"
run_with_retry "${GIT_ATTEMPTS}" "${GIT_RETRY_DELAY_SECONDS}" \
  git merge --ff-only "refs/remotes/origin/${BRANCH}"

if [[ "${RUN_MIGRATION_COMPAT_CHECK}" == "1" ]]; then
  DEPLOY_PHASE="migration_compat_check"
  ALLOW_BREAKING_MIGRATIONS="${ALLOW_BREAKING_MIGRATIONS}" \
    python3 ./scripts/check_migration_backward_compat.py \
    --base "${PRE_DEPLOY_COMMIT}" \
    --head "$(git rev-parse HEAD)"
fi

DEPLOY_PHASE="venv_prepare"
if [[ ! -d "${VENV_DIR}" ]]; then
  log "Creating virtual environment at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

DEPLOY_PHASE="pip_install"
log "Installing dependencies"
run_with_retry "${PIP_ATTEMPTS}" "${PIP_RETRY_DELAY_SECONDS}" \
  pip install --disable-pip-version-check -r requirements.txt

DEPLOY_PHASE="db_migrate"
log "Running DB migrations"
alembic upgrade head

if [[ "${RUN_TESTS}" == "1" ]]; then
  DEPLOY_PHASE="tests"
  log "Running tests"
  if [[ -f requirements-dev.txt ]]; then
    pip install -r requirements-dev.txt
  fi
  pytest -q
fi

if command -v systemctl >/dev/null 2>&1; then
  DEPLOY_PHASE="service_restart"
  log "Restarting service ${SERVICE_NAME}"
  run_systemctl restart "${SERVICE_NAME}"
  run_systemctl is-active --quiet "${SERVICE_NAME}"
  log "Service is active: ${SERVICE_NAME}"
else
  log "systemctl not found; skipping service restart"
fi

DEPLOY_PHASE="post_restart_health"
run_health_check

DEPLOY_PHASE="post_restart_smoke"
CURRENT_SHORT="$(git rev-parse --short HEAD)"
run_post_deploy_smoke "${CURRENT_SHORT}"

DEPLOY_PHASE="complete"
if [[ "${DEPLOY_ALERT_ON_SUCCESS}" == "1" ]]; then
  notify_alert "Deploy succeeded on ${SERVICE_NAME} at commit ${CURRENT_SHORT}."
fi
log "Deployed commit ${CURRENT_SHORT}"
