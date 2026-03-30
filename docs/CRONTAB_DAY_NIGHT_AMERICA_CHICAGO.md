# Crontab Templates (America/Chicago)

Date: March 30, 2026

Purpose: ready-to-paste cron entries for `myDNAobv` day/night export scheduling with explicit Central Time handling.

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

# Day profile: single export lane during daytime.
*/2 7-22 * * * flock -n /var/lock/mydnaobv_export_day.lock timeout 120s nice -n 15 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once

# Night profile: dual export lanes for backlog/rebuild throughput.
*/2 23,0-6 * * * flock -n /var/lock/mydnaobv_export_night_a.lock timeout 150s nice -n 8 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once
*/2 23,0-6 * * * flock -n /var/lock/mydnaobv_export_night_b.lock timeout 150s nice -n 8 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once

# Daily cleanup.
17 3 * * * /usr/bin/python3 -m app.exports.worker --cleanup
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

# Day profile: single export lane during daytime.
*/2 7-22 * * * flock -n /var/lock/mydnaobv_export_day.lock timeout 120s nice -n 15 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once

# Night profile: dual export lanes for backlog/rebuild throughput.
*/2 23,0-6 * * * flock -n /var/lock/mydnaobv_export_night_a.lock timeout 150s nice -n 8 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once
*/2 23,0-6 * * * flock -n /var/lock/mydnaobv_export_night_b.lock timeout 150s nice -n 8 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once

# Daily cleanup.
17 3 * * * /usr/bin/python3 -m app.exports.worker --cleanup
```

Verify:

```bash
sudo crontab -l
```

## Notes

- If an older single-lane worker line exists (`mydnaobv_export.lock`), remove it when enabling this profile.
- Keep iNaturalist request/media guardrail environment values unchanged while applying this scheduler.
- Daylight Saving Time is handled automatically by `CRON_TZ=America/Chicago` (CST/CDT).
