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
SSH_OPTS="${SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=8}"
PRECHECK_DNS="${PRECHECK_DNS:-1}"
PRECHECK_SSH="${PRECHECK_SSH:-1}"
PRECHECK_SUDO="${PRECHECK_SUDO:-1}"
EXPECTED_HOST_IP="${EXPECTED_HOST_IP:-}"

if [[ -z "${HOST}" ]]; then
  echo "HOST is required (example: HOST=dna.mrdbid.com ./scripts/deploy_remote.sh)" >&2
  exit 1
fi

log() {
  printf '[deploy-remote] %s\n' "$*"
}

is_likely_cloudflare_proxy_ip() {
  local ip="$1"
  [[ "${ip}" =~ ^104\.(1[6-9]|2[0-3])\. ]] && return 0
  [[ "${ip}" =~ ^172\.(6[4-9]|7[0-1])\. ]] && return 0
  [[ "${ip}" =~ ^162\.158\. ]] && return 0
  [[ "${ip}" =~ ^188\.(114|115)\. ]] && return 0
  [[ "${ip}" =~ ^198\.41\. ]] && return 0
  return 1
}

declare -a ssh_opts_arr
# shellcheck disable=SC2206
ssh_opts_arr=( ${SSH_OPTS} )

if [[ "${PRECHECK_DNS}" == "1" ]]; then
  if ! command -v dig >/dev/null 2>&1; then
    log "Skipping DNS precheck: dig not found."
  else
    resolved_ips=()
    while IFS= read -r ip; do
      [[ -n "${ip}" ]] && resolved_ips+=("${ip}")
    done < <(dig +short A "${HOST}" | awk 'NF')
    if [[ "${#resolved_ips[@]}" -eq 0 ]]; then
      log "DNS precheck failed: no A records for ${HOST}."
      exit 1
    fi

    log "DNS A records for ${HOST}: ${resolved_ips[*]}"

    if [[ -n "${EXPECTED_HOST_IP}" ]]; then
      found_expected=0
      for ip in "${resolved_ips[@]}"; do
        if [[ "${ip}" == "${EXPECTED_HOST_IP}" ]]; then
          found_expected=1
          break
        fi
      done
      if [[ "${found_expected}" != "1" ]]; then
        log "DNS precheck failed: expected ${EXPECTED_HOST_IP}, got ${resolved_ips[*]}."
        exit 1
      fi
    else
      all_cf=1
      for ip in "${resolved_ips[@]}"; do
        if ! is_likely_cloudflare_proxy_ip "${ip}"; then
          all_cf=0
          break
        fi
      done
      if [[ "${all_cf}" == "1" ]]; then
        log "DNS precheck failed: ${HOST} resolves only to likely Cloudflare proxy IPs."
        log "Set host record to DNS only (gray cloud) or set EXPECTED_HOST_IP explicitly."
        exit 1
      fi
    fi
  fi
fi

if [[ "${PRECHECK_SSH}" == "1" ]]; then
  log "Checking SSH key authentication for ${USER_NAME}@${HOST}"
  ssh "${ssh_opts_arr[@]}" "${USER_NAME}@${HOST}" "echo SSH key auth ok" >/dev/null
fi

if [[ "${PRECHECK_SUDO}" == "1" && "${SYSTEMCTL_USE_SUDO}" == "1" ]]; then
  log "Checking non-interactive sudo for systemctl ${SERVICE_NAME}"
  ssh "${ssh_opts_arr[@]}" "${USER_NAME}@${HOST}" \
    "sudo -n systemctl is-active --quiet '${SERVICE_NAME}'; rc=\$?; test \$rc -eq 0 -o \$rc -eq 3"
fi

ssh "${ssh_opts_arr[@]}" "${USER_NAME}@${HOST}" \
  "cd '${APP_DIR}' && \
   APP_DIR='${APP_DIR}' \
   BRANCH='${BRANCH}' \
   SERVICE_NAME='${SERVICE_NAME}' \
   SYSTEMCTL_USE_SUDO='${SYSTEMCTL_USE_SUDO}' \
   RUN_TESTS='${RUN_TESTS}' \
   ALLOW_DIRTY='${ALLOW_DIRTY}' \
   HEALTHCHECK_URL='${HEALTHCHECK_URL}' \
   ./scripts/deploy_server.sh"
