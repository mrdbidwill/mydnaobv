# Crontab Templates (America/Chicago)

Date: May 15, 2026

Purpose: ready-to-paste cron entries for `myDNAobv` backlog push + steady-state scheduling with explicit Central Time handling.

## Rollback Triggers — Switch to Steady-State Immediately When

- CPU >85% for 10+ minutes (Hostinger will email you)
- Load average exceeds total vCPU count for 10+ minutes
- Disk free < 15 GB on root filesystem
- Web app 5xx rate > 1%

When any of the above occurs: comment out the backlog lines, uncomment the steady-state lines.

## Choose One Owner

Use only one owner for these jobs:
- `root` crontab, or
- `mydnaobv` crontab.

Do not install in both, or you will double-run workers.

## worker_guarded.sh

All cron entries use `scripts/worker_guarded.sh` as a wrapper that silently skips the
worker launch if CPU load or disk free space are outside safe bounds. This prevents
piling-on during stress without needing manual intervention.

Guard thresholds (override via environment in `.env` or cron environment block):
- `WORKER_MIN_FREE_GB=10` — skip if less than 10 GB free on `EXPORT_STORAGE_DIR`
- `WORKER_MAX_LOAD_FACTOR=0.85` — skip if load/vCPU ratio ≥ 0.85
- `WORKER_GUARD_LOG=$EXPORT_STORAGE_DIR/worker_guard.log` — skip events are logged here

## Option A: Install Under `mydnaobv` (Recommended)

Check current entries:

```bash
crontab -l
```

Edit:

```bash
crontab -e
```

Paste this block:

```cron
CRON_TZ=America/Chicago
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Backlog push profile (temporary): three lanes every minute.
# DISABLE when CPU alert fires or disk < 15 GB free. Switch to steady-state below.
# * * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_a.lock timeout 900s nice -n 6 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once
# * * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_b.lock timeout 900s nice -n 6 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once
# * * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_c.lock timeout 900s nice -n 6 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once

# Steady-state profile: one daytime lane and two night lanes.
*/2 7-22 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_day.lock timeout 600s nice -n 12 ionice -c2 -n7 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once
*/2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_a.lock timeout 900s nice -n 8 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once
*/2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_b.lock timeout 900s nice -n 8 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once

# Hourly cleanup (keeps retention-based disk use in check).
47 * * * * cd /opt/mydnaobv/app && .venv/bin/python3 -m app.exports.worker --cleanup >> /opt/mydnaobv/exports/cleanup_hourly.log 2>&1

# Daily cleanup (authoritative sweep).
17 3 * * * cd /opt/mydnaobv/app && .venv/bin/python3 -m app.exports.worker --cleanup >> /opt/mydnaobv/exports/cleanup.log 2>&1

# Monthly DB backup restore verification.
45 4 1 * * cd /opt/mydnaobv/app && scripts/run_restore_verify_from_env.sh >> /opt/mydnaobv/exports/restore_verify.log 2>&1
```

Verify:

```bash
crontab -l
```

## Option B: Install Under `root`

Check current entries:

```bash
sudo crontab -l
```

Edit:

```bash
sudo crontab -e
```

Paste this block (identical content, different owner):

```cron
CRON_TZ=America/Chicago
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Backlog push profile (temporary): three lanes every minute.
# DISABLE when CPU alert fires or disk < 15 GB free. Switch to steady-state below.
# * * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_a.lock timeout 900s nice -n 6 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once
# * * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_b.lock timeout 900s nice -n 6 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once
# * * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_c.lock timeout 900s nice -n 6 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once

# Steady-state profile: one daytime lane and two night lanes.
*/2 7-22 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_day.lock timeout 600s nice -n 12 ionice -c2 -n7 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once
*/2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_a.lock timeout 900s nice -n 8 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once
*/2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_b.lock timeout 900s nice -n 8 ionice -c2 -n6 scripts/worker_guarded.sh .venv/bin/python -m app.exports.worker --once

# Hourly cleanup (keeps retention-based disk use in check).
47 * * * * cd /opt/mydnaobv/app && .venv/bin/python3 -m app.exports.worker --cleanup >> /opt/mydnaobv/exports/cleanup_hourly.log 2>&1

# Daily cleanup (authoritative sweep).
17 3 * * * cd /opt/mydnaobv/app && .venv/bin/python3 -m app.exports.worker --cleanup >> /opt/mydnaobv/exports/cleanup.log 2>&1

# Monthly DB backup restore verification.
45 4 1 * * cd /opt/mydnaobv/app && scripts/run_restore_verify_from_env.sh >> /opt/mydnaobv/exports/restore_verify.log 2>&1
```

Verify:

```bash
sudo crontab -l
```

## Notes

- All worker entries use `scripts/worker_guarded.sh` as a CPU/disk guard wrapper.
- Use either backlog profile or steady-state profile, not both at once.
- Backlog profile is TEMPORARY. Switch to steady-state once the queue age is healthy.
- The guard script logs skipped launches to `worker_guard.log` in the exports directory.
- `EXPORT_PUBLISHED_RETENTION_HOURS` (default 4) controls how quickly local job
  directories are removed after successful R2 upload. Increase if you need longer
  local fallback availability after publish.
- Daylight Saving Time is handled automatically by `CRON_TZ=America/Chicago` (CST/CDT).
- Queue guardrail target: oldest runnable queued export under 10 minutes during backlog
  push (`waiting_quota` retries can remain scheduled into the future by design).
