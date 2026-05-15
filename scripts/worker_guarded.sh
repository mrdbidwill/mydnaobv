#!/usr/bin/env bash
# worker_guarded.sh — launch the export worker only when CPU and disk are healthy.
#
# Drop-in wrapper for cron entries. Replaces the bare python invocation so that
# a worker is silently skipped (rather than pile-driving a stressed system) when
# either the CPU load or available disk space is outside safe bounds.
#
# Usage (in crontab):
#   flock -n /var/lock/mydnaobv_export_day.lock timeout 600s nice -n 12 ionice -c2 -n7 \
#       /opt/mydnaobv/app/scripts/worker_guarded.sh \
#       /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
#
# Environment variables (all optional — sensible defaults below):
#   EXPORT_STORAGE_DIR      path used for the disk-free check (default: /opt/mydnaobv/exports)
#   WORKER_MIN_FREE_GB      minimum free GB before skipping launch (default: 10)
#   WORKER_MAX_LOAD_FACTOR  load-per-vCPU ratio above which we skip (default: 0.85)
#   WORKER_GUARD_LOG        log file path for skip events (default: $EXPORT_STORAGE_DIR/worker_guard.log)

set -eo pipefail

STORAGE_DIR="${EXPORT_STORAGE_DIR:-/opt/mydnaobv/exports}"
LOG_FILE="${WORKER_GUARD_LOG:-${STORAGE_DIR}/worker_guard.log}"
MIN_FREE_GB="${WORKER_MIN_FREE_GB:-10}"
MAX_LOAD_FACTOR="${WORKER_MAX_LOAD_FACTOR:-0.85}"

_ts()  { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
_log() { echo "$(_ts) $*" >> "${LOG_FILE}" 2>/dev/null || true; }

# --- Disk check ---
free_kb=$(df -k "${STORAGE_DIR}" 2>/dev/null | awk 'NR==2 {print $4}')
if [ -z "${free_kb}" ]; then
    _log "WARN disk check failed for ${STORAGE_DIR}; proceeding anyway"
else
    min_kb=$(( MIN_FREE_GB * 1024 * 1024 ))
    if [ "${free_kb}" -lt "${min_kb}" ]; then
        free_gb=$(awk "BEGIN {printf \"%.1f\", ${free_kb}/1048576}")
        _log "SKIP free disk ${free_gb} GB < ${MIN_FREE_GB} GB required"
        exit 0
    fi
fi

# --- CPU load check ---
cpus=$(nproc 2>/dev/null || grep -c '^processor' /proc/cpuinfo 2>/dev/null || echo 2)
load_1m=$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo 0)
should_skip=$(awk -v load="${load_1m}" -v cpus="${cpus}" -v factor="${MAX_LOAD_FACTOR}" \
    'BEGIN { print (load / cpus >= factor) ? 1 : 0 }')
if [ "${should_skip}" = "1" ]; then
    _log "SKIP load ${load_1m} / ${cpus} vCPUs (factor ${MAX_LOAD_FACTOR})"
    exit 0
fi

exec "$@"
