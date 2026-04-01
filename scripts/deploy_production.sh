#!/usr/bin/env bash
set -euo pipefail

# Canonical one-command production deploy entrypoint.
# Override values via environment variables only when needed.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

HOST="${HOST:-dna.mrdbid.com}"
USER_NAME="${USER_NAME:-mydnaobv}"
APP_DIR="${APP_DIR:-/opt/mydnaobv/app}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-mydnaobv}"
HEALTHCHECK_HOST_HEADER="${HEALTHCHECK_HOST_HEADER:-dna.mrdbid.com}"
EXPECTED_HOST_IP="${EXPECTED_HOST_IP:-85.31.233.192}"

exec env \
  HOST="${HOST}" \
  USER_NAME="${USER_NAME}" \
  APP_DIR="${APP_DIR}" \
  BRANCH="${BRANCH}" \
  SERVICE_NAME="${SERVICE_NAME}" \
  HEALTHCHECK_HOST_HEADER="${HEALTHCHECK_HOST_HEADER}" \
  EXPECTED_HOST_IP="${EXPECTED_HOST_IP}" \
  "${SCRIPT_DIR}/deploy_remote.sh"
