#!/usr/bin/env bash
set -euo pipefail

# Deploy myDNAobv on the server host.
# Intended default layout from runbooks:
# - repo path: /opt/mydnaobv/app
# - service: mydnaobv

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

log() {
  printf '[deploy] %s\n' "$*"
}

run_systemctl() {
  if [[ "${SYSTEMCTL_USE_SUDO}" == "1" ]]; then
    if ! sudo -n systemctl "$@"; then
      log "systemctl via sudo failed for command: systemctl $*"
      log "Ensure deploy user has NOPASSWD sudo for required service commands."
      exit 1
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

if [[ ! -d "${APP_DIR}" ]]; then
  log "APP_DIR does not exist: ${APP_DIR}"
  exit 1
fi

cd "${APP_DIR}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "APP_DIR is not a git repository: ${APP_DIR}"
  exit 1
fi

if [[ "${ALLOW_DIRTY}" != "1" ]]; then
  if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
    log "Repository has tracked uncommitted changes; aborting. Set ALLOW_DIRTY=1 to bypass."
    git status --short
    exit 1
  fi

  if [[ "${ALLOW_UNTRACKED}" != "1" ]] && [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    log "Repository has untracked files; aborting. Set ALLOW_UNTRACKED=1 to bypass."
    git status --short
    exit 1
  fi
fi

log "Fetching latest ${BRANCH}"
run_with_retry "${GIT_ATTEMPTS}" "${GIT_RETRY_DELAY_SECONDS}" git fetch origin "${BRANCH}"
git checkout "${BRANCH}"
run_with_retry "${GIT_ATTEMPTS}" "${GIT_RETRY_DELAY_SECONDS}" git pull --ff-only origin "${BRANCH}"

if [[ ! -d "${VENV_DIR}" ]]; then
  log "Creating virtual environment at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

log "Installing dependencies"
run_with_retry "${PIP_ATTEMPTS}" "${PIP_RETRY_DELAY_SECONDS}" \
  pip install --disable-pip-version-check -r requirements.txt

log "Running DB migrations"
alembic upgrade head

if [[ "${RUN_TESTS}" == "1" ]]; then
  log "Running tests"
  if [[ -f requirements-dev.txt ]]; then
    pip install -r requirements-dev.txt
  fi
  pytest -q
fi

if command -v systemctl >/dev/null 2>&1; then
  log "Restarting service ${SERVICE_NAME}"
  run_systemctl restart "${SERVICE_NAME}"
  run_systemctl is-active --quiet "${SERVICE_NAME}"
  log "Service is active: ${SERVICE_NAME}"
else
  log "systemctl not found; skipping service restart"
fi

if command -v curl >/dev/null 2>&1; then
  log "Health check ${HEALTHCHECK_URL}"
  health_ok=0
  for attempt in $(seq 1 "${HEALTHCHECK_ATTEMPTS}"); do
    if [[ -n "${HEALTHCHECK_HOST_HEADER}" ]]; then
      if curl --fail --silent --show-error --max-time 20 \
        -H "Host: ${HEALTHCHECK_HOST_HEADER}" \
        "${HEALTHCHECK_URL}" >/dev/null; then
        health_ok=1
        break
      fi
    else
      if curl --fail --silent --show-error --max-time 20 "${HEALTHCHECK_URL}" >/dev/null; then
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
    exit 1
  fi
else
  log "curl not found; skipping health check"
fi

log "Deployed commit $(git rev-parse --short HEAD)"
