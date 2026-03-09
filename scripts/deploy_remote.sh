#!/usr/bin/env bash
set -euo pipefail

# Run deploy_server.sh on a remote host over SSH.
#
# Example:
# HOST=your.server.tld USER=mydnaobv APP_DIR=/opt/mydnaobv/app \
#   ./scripts/deploy_remote.sh

HOST="${HOST:-}"
USER_NAME="${USER_NAME:-mydnaobv}"
APP_DIR="${APP_DIR:-/opt/mydnaobv/app}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-mydnaobv}"
SYSTEMCTL_USE_SUDO="${SYSTEMCTL_USE_SUDO:-1}"
RUN_TESTS="${RUN_TESTS:-0}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1/}"
SSH_OPTS="${SSH_OPTS:-}"

if [[ -z "${HOST}" ]]; then
  echo "HOST is required (example: HOST=dna.mrdbid.com ./scripts/deploy_remote.sh)" >&2
  exit 1
fi

ssh ${SSH_OPTS} "${USER_NAME}@${HOST}" \
  "cd '${APP_DIR}' && \
   APP_DIR='${APP_DIR}' \
   BRANCH='${BRANCH}' \
   SERVICE_NAME='${SERVICE_NAME}' \
   SYSTEMCTL_USE_SUDO='${SYSTEMCTL_USE_SUDO}' \
   RUN_TESTS='${RUN_TESTS}' \
   ALLOW_DIRTY='${ALLOW_DIRTY}' \
   HEALTHCHECK_URL='${HEALTHCHECK_URL}' \
   ./scripts/deploy_server.sh"
