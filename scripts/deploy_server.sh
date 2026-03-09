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
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
RUN_TESTS="${RUN_TESTS:-0}"
SYSTEMCTL_USE_SUDO="${SYSTEMCTL_USE_SUDO:-1}"

log() {
  printf '[deploy] %s\n' "$*"
}

run_systemctl() {
  if [[ "${SYSTEMCTL_USE_SUDO}" == "1" ]]; then
    sudo systemctl "$@"
  else
    systemctl "$@"
  fi
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

if [[ "${ALLOW_DIRTY}" != "1" ]] && [[ -n "$(git status --porcelain)" ]]; then
  log "Repository has uncommitted changes; aborting. Set ALLOW_DIRTY=1 to bypass."
  git status --short
  exit 1
fi

log "Fetching latest ${BRANCH}"
git fetch origin "${BRANCH}"
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

if [[ ! -d "${VENV_DIR}" ]]; then
  log "Creating virtual environment at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

log "Installing dependencies"
pip install --upgrade pip
pip install -r requirements.txt

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
  curl --fail --silent --show-error --max-time 20 "${HEALTHCHECK_URL}" >/dev/null
else
  log "curl not found; skipping health check"
fi

log "Deployed commit $(git rev-parse --short HEAD)"
