#!/usr/bin/env bash
# check_pipeline_health.sh — operational monitoring for the myDNAobv export pipeline.
#
# Run manually at any time:
#   bash scripts/check_pipeline_health.sh
#
# Or add to crontab for a daily digest (runs locally, requires curl + jq or python3):
#   0 8 * * * cd /path/to/myDNAobv && bash scripts/check_pipeline_health.sh 2>&1 | tee -a /tmp/pipeline_health.log
#
# Override the base URL and credentials via environment variables:
#   BASE_URL=https://dna.mrdbid.com ADMIN_USER=admin ADMIN_PASS=secret bash scripts/check_pipeline_health.sh

set -euo pipefail

BASE_URL="${BASE_URL:-https://dna.mrdbid.com}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-}"

# Thresholds that trigger a WARNING or FAIL exit code
WARN_DISK_FREE_GB="${WARN_DISK_FREE_GB:-12}"
FAIL_DISK_FREE_GB="${FAIL_DISK_FREE_GB:-8}"
WARN_OVERDUE_IDLE="${WARN_OVERDUE_IDLE:-5}"    # lists overdue with no active job
WARN_WAITING_QUOTA_HOURS="${WARN_WAITING_QUOTA_HOURS:-4}"  # hours before a stuck waiting_quota is a warning
FAIL_LAST_COMPLETED_HOURS="${FAIL_LAST_COMPLETED_HOURS:-24}"  # no completion in this many hours = fail
# Number of public download links to spot-check end-to-end (0 to skip)
SPOT_CHECK_COUNT="${SPOT_CHECK_COUNT:-3}"

# ── helpers ──────────────────────────────────────────────────────────────────

OK=0; WARNINGS=0; FAILURES=0

ok()   { printf '%s\n' "  [OK]   $*"; }
warn() { printf '%s\n' "  [WARN] $*"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '%s\n' "  [FAIL] $*"; FAILURES=$((FAILURES + 1)); }
info() { printf '%s\n' "         $*"; }

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'ERROR: %s is required but not found.\n' "$1" >&2
    exit 2
  fi
}

api_get() {
  local path="$1"
  curl -s --max-time 30 --fail-with-body \
    -u "${ADMIN_USER}:${ADMIN_PASS}" \
    "${BASE_URL%/}${path}"
}

json_field() {
  # Usage: json_field <json_string> <field_path>
  # Uses python3 as a portable JSON parser (no jq dependency required)
  local json="$1" field="$2"
  python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
parts = '${field}'.split('.')
for p in parts:
    if p.isdigit():
        data = data[int(p)]
    else:
        data = data.get(p) if isinstance(data, dict) else None
    if data is None:
        break
print(data if data is not None else '')
" <<< "${json}"
}

hours_since_iso() {
  # Returns fractional hours since an ISO timestamp, or empty string if input is empty
  local ts="$1"
  if [[ -z "${ts}" ]]; then echo ""; return; fi
  python3 -c "
from datetime import datetime, UTC
ts = '${ts}'.replace('Z', '+00:00')
try:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    diff = (datetime.now(UTC) - dt).total_seconds() / 3600
    print(f'{diff:.1f}')
except Exception as e:
    print('')
"
}

check_url_reachable() {
  local url="$1"
  local http_code
  http_code=$(curl -s -L --max-time 30 -o /dev/null -w '%{http_code}' "${url}" 2>/dev/null || echo "000")
  [[ "${http_code}" =~ ^2 ]]
}

# ── main ─────────────────────────────────────────────────────────────────────

require_cmd curl
require_cmd python3

printf '\n=== myDNAobv Pipeline Health Check ===\n'
printf 'Base URL : %s\n' "${BASE_URL}"
printf 'Time     : %s\n\n' "$(date -u '+%Y-%m-%d %H:%M UTC')"

# ── 1. Queue status ───────────────────────────────────────────────────────────
echo "-- Queue Status --"
STATUS_JSON="$(api_get /admin/queue-status)" || {
  fail "Could not reach /admin/queue-status (check URL and credentials)"
  printf '\nFailed: %d  Warnings: %d\n' "${FAILURES}" "${WARNINGS}"
  exit 1
}

TOTAL_ACTIVE="$(json_field "${STATUS_JSON}" "total_active" <<< "${STATUS_JSON}")"
BY_STATUS_RUNNING="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('by_status',{}).get('running',0))" <<< "${STATUS_JSON}")"
BY_STATUS_QUEUED="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('by_status',{}).get('queued',0))" <<< "${STATUS_JSON}")"
BY_STATUS_WAITING="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('by_status',{}).get('waiting_quota',0))" <<< "${STATUS_JSON}")"
BY_STATUS_READY="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('by_status',{}).get('ready',0))" <<< "${STATUS_JSON}")"
BY_STATUS_PARTIAL="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('by_status',{}).get('partial_ready',0))" <<< "${STATUS_JSON}")"
BY_STATUS_FAILED="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('by_status',{}).get('failed',0))" <<< "${STATUS_JSON}")"

info "running=${BY_STATUS_RUNNING}  queued=${BY_STATUS_QUEUED}  waiting_quota=${BY_STATUS_WAITING}  ready=${BY_STATUS_READY}  partial_ready=${BY_STATUS_PARTIAL}  failed=${BY_STATUS_FAILED}"

OLDEST_WAITING="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('oldest_waiting_quota_next_run') or '')" <<< "${STATUS_JSON}")"
if [[ -n "${OLDEST_WAITING}" ]]; then
  WAIT_HOURS="$(hours_since_iso "${OLDEST_WAITING}")"
  if python3 -c "exit(0 if float('${WAIT_HOURS:-0}') > ${WARN_WAITING_QUOTA_HOURS} else 1)" 2>/dev/null; then
    warn "Oldest waiting_quota next_run is ${WAIT_HOURS}h ago (${OLDEST_WAITING}) — jobs may be stuck"
  else
    ok "waiting_quota jobs next_run: ${OLDEST_WAITING}"
  fi
else
  ok "No jobs in waiting_quota"
fi

LAST_COMPLETED_AT="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); j=d.get('last_completed_job') or {}; print(j.get('finished_at') or '')" <<< "${STATUS_JSON}")"
if [[ -n "${LAST_COMPLETED_AT}" ]]; then
  COMPLETED_HOURS="$(hours_since_iso "${LAST_COMPLETED_AT}")"
  if python3 -c "exit(0 if float('${COMPLETED_HOURS:-0}') > ${FAIL_LAST_COMPLETED_HOURS} else 1)" 2>/dev/null; then
    fail "Last completed job was ${COMPLETED_HOURS}h ago — pipeline may have stopped"
  else
    ok "Last job completed ${COMPLETED_HOURS}h ago"
  fi
else
  warn "No completed jobs found yet"
fi

# ── 2. Disk space ─────────────────────────────────────────────────────────────
printf "\n"; echo "-- Disk Space --"
DISK_FREE="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('disk_free_gb') or '')" <<< "${STATUS_JSON}")"
if [[ -n "${DISK_FREE}" ]]; then
  if python3 -c "exit(0 if float('${DISK_FREE}') < ${FAIL_DISK_FREE_GB} else 1)" 2>/dev/null; then
    fail "Disk free ${DISK_FREE}GB — BELOW minimum ${FAIL_DISK_FREE_GB}GB (jobs will pause)"
  elif python3 -c "exit(0 if float('${DISK_FREE}') < ${WARN_DISK_FREE_GB} else 1)" 2>/dev/null; then
    warn "Disk free ${DISK_FREE}GB — approaching minimum ${FAIL_DISK_FREE_GB}GB"
  else
    ok "Disk free: ${DISK_FREE}GB"
  fi
else
  warn "Disk free info unavailable"
fi

# ── 3. List health ────────────────────────────────────────────────────────────
printf "\n"; echo "-- List Freshness --"
HEALTH_JSON="$(api_get /admin/list-health)" || {
  warn "Could not reach /admin/list-health"
  HEALTH_JSON="{}"
}

TOTAL_LISTS="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('summary',{}).get('total',0))" <<< "${HEALTH_JSON}")"
FRESH="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('summary',{}).get('fresh',0))" <<< "${HEALTH_JSON}")"
OVERDUE_IDLE="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('summary',{}).get('overdue_idle',0))" <<< "${HEALTH_JSON}")"
OVERDUE_QUEUED="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('summary',{}).get('overdue_queued',0))" <<< "${HEALTH_JSON}")"
NEVER="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('summary',{}).get('never_exported',0))" <<< "${HEALTH_JSON}")"
OLDEST_EXPORT="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('list_health',{}).get('oldest_export_at') or '')" <<< "${STATUS_JSON}" 2>/dev/null || echo "")"

info "total=${TOTAL_LISTS}  fresh=${FRESH}  overdue_idle=${OVERDUE_IDLE}  overdue_queued=${OVERDUE_QUEUED}  never_exported=${NEVER}"

if [[ "${NEVER}" -gt 0 ]]; then
  warn "${NEVER} public list(s) have never been exported"
fi

if python3 -c "exit(0 if int('${OVERDUE_IDLE}') > ${WARN_OVERDUE_IDLE} else 1)" 2>/dev/null; then
  fail "${OVERDUE_IDLE} lists are overdue for refresh with no active job — auto-refresh may not be running"
elif [[ "${OVERDUE_IDLE}" -gt 0 ]]; then
  warn "${OVERDUE_IDLE} list(s) overdue but not yet queued (will queue on next worker cycle)"
else
  ok "No idle overdue lists"
fi

if [[ "${OVERDUE_QUEUED}" -gt 0 ]]; then
  ok "${OVERDUE_QUEUED} overdue list(s) already have active jobs (in progress)"
fi

# Show names of overdue idle lists if any
if [[ "${OVERDUE_IDLE}" -gt 0 ]]; then
  info "Overdue idle lists:"
  python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
for r in d.get('overdue_idle', [])[:10]:
    title = r.get('title','?')
    days = r.get('days_since_export')
    age = f'{days}d ago' if days is not None else 'never'
    print(f'    {title} ({age})')
" <<< "${HEALTH_JSON}"
fi

# ── 4. Worker activity ────────────────────────────────────────────────────────
printf "\n"; echo "-- Worker Activity --"
LAST_RUN="$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('last_worker_run') or '')" <<< "${STATUS_JSON}")"
if [[ -n "${LAST_RUN}" ]]; then
  ok "Last worker run: ${LAST_RUN}"
else
  warn "No worker run log found — cron may not be active or this is a fresh deploy"
fi

# ── 5. Key config ─────────────────────────────────────────────────────────────
printf "\n"; echo "-- Active Config --"
python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
cfg = d.get('config', {})
for k, v in cfg.items():
    print(f'  {k}={v}')
" <<< "${STATUS_JSON}"

# ── 6. Spot-check live download links ─────────────────────────────────────────
if [[ "${SPOT_CHECK_COUNT}" -gt 0 ]]; then
  printf '\n'; echo "-- Live Download Spot Check (${SPOT_CHECK_COUNT} links) --"
  SPOT_LINKS="$(python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
rows = d.get('all', [])
# Pick the first N that have been exported
links = []
for r in rows:
    if r.get('last_exported_at') and r.get('list_id'):
        links.append(r['list_id'])
    if len(links) >= ${SPOT_CHECK_COUNT}:
        break
print('\n'.join(str(l) for l in links))
" <<< "${HEALTH_JSON}")"

  SPOT_FAIL=0
  while IFS= read -r list_id; do
    [[ -z "${list_id}" ]] && continue
    # Fetch the public downloads page to find an artifact link for this list
    PAGE_JSON="$(api_get "/public/lists/${list_id}/artifacts" 2>/dev/null || echo "{}")"
    # Fall back to trying the downloads page for a link
    ARTIFACT_PATH="$(python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
arts = d.get('artifacts', [])
for a in arts:
    if a.get('kind') in ('observations_index_pdf', 'merged_pdf'):
        print(f\"/public/lists/${list_id}/artifacts/{a['id']}/download\")
        break
" <<< "${PAGE_JSON}" 2>/dev/null || echo "")"

    if [[ -n "${ARTIFACT_PATH}" ]]; then
      if check_url_reachable "${BASE_URL%/}${ARTIFACT_PATH}"; then
        ok "list ${list_id}: download reachable"
      else
        fail "list ${list_id}: download returned error — ${BASE_URL%/}${ARTIFACT_PATH}"
        SPOT_FAIL=$((SPOT_FAIL + 1))
      fi
    else
      # No artifact API — just probe the downloads page for this list
      if check_url_reachable "${BASE_URL%/}/downloads?state=AL"; then
        ok "list ${list_id}: downloads page reachable (artifact API not available)"
      else
        warn "list ${list_id}: could not find artifact link to spot-check"
      fi
    fi
  done <<< "${SPOT_LINKS}"
fi

# ── Result ────────────────────────────────────────────────────────────────────
printf "\n========================================\n"
printf 'Result: %d failure(s), %d warning(s)\n' "${FAILURES}" "${WARNINGS}"

if [[ "${FAILURES}" -gt 0 ]]; then
  printf 'Status: FAIL\n\n'
  exit 1
elif [[ "${WARNINGS}" -gt 0 ]]; then
  printf 'Status: WARN\n\n'
  exit 0
else
  printf 'Status: OK\n\n'
  exit 0
fi
