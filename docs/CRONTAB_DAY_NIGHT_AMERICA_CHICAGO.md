# Crontab Templates (America/Chicago)

Date: May 10, 2026

Purpose: ready-to-paste cron entries for `myDNAobv` backlog push + steady-state scheduling with explicit Central Time handling.

## Choose One Owner

Use only one owner for these jobs:
- `root` crontab, or
- `mydnaobv` crontab.

Do not install in both, or you will double-run workers.

## Option A: Install Under `mydnaobv` (Recommended)

Check current entries:

```bash
sudo -u mydnaobv -H crontab -l
```

Edit:

```bash
sudo -u mydnaobv -H crontab -e
```

Paste this block:

```cron
CRON_TZ=America/Chicago
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Backlog push profile (temporary): three lanes every minute.
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_a.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_b.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_c.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once

# Steady-state profile: one daytime lane and two night lanes.
# Keep these commented while backlog profile is active.
# */2 7-22 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_day.lock timeout 600s nice -n 12 ionice -c2 -n7 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
# */2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_a.lock timeout 900s nice -n 8 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
# */2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_b.lock timeout 900s nice -n 8 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once

# Daily cleanup.
17 3 * * * cd /opt/mydnaobv/app && /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --cleanup

# Monthly DB backup restore verification.
45 4 1 * * cd /opt/mydnaobv/app && /opt/mydnaobv/app/scripts/run_restore_verify_from_env.sh >> /opt/mydnaobv/exports/restore_verify.log 2>&1
```

Verify:

```bash
sudo -u mydnaobv -H crontab -l
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

Paste this block:

```cron
CRON_TZ=America/Chicago
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Backlog push profile (temporary): three lanes every minute.
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_a.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_b.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_c.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once

# Steady-state profile: one daytime lane and two night lanes.
# Keep these commented while backlog profile is active.
# */2 7-22 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_day.lock timeout 600s nice -n 12 ionice -c2 -n7 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
# */2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_a.lock timeout 900s nice -n 8 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
# */2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_b.lock timeout 900s nice -n 8 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once

# Daily cleanup.
17 3 * * * cd /opt/mydnaobv/app && /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --cleanup

# Monthly DB backup restore verification.
45 4 1 * * cd /opt/mydnaobv/app && /opt/mydnaobv/app/scripts/run_restore_verify_from_env.sh >> /opt/mydnaobv/exports/restore_verify.log 2>&1
```

Verify:

```bash
sudo crontab -l
```

## Notes

- `cd /opt/mydnaobv/app` and venv python are required so module imports and env loading stay consistent in cron.
- Use either backlog profile or steady-state profile, not both at once.
- When queue age is healthy again, disable backlog lines and enable steady-state lines.
- Keep iNaturalist request/media guardrail environment values unchanged while applying this scheduler.
- For Stage 3 retry damping after deferred-sync cache exports, set `EXPORT_SYNC_DEFER_RETRY_MINUTES` in `.env` (default `360`).
- Daylight Saving Time is handled automatically by `CRON_TZ=America/Chicago` (CST/CDT).
- Queue guardrail target: oldest runnable queued export under 10 minutes during backlog push (`waiting_quota` retries can remain scheduled into the future by design).
