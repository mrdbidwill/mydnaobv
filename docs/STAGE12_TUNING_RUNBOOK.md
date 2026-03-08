# Stage 1/2 Tuning Runbook

Date: March 8, 2026

Goal: increase queue throughput while keeping iNaturalist safety limits unchanged.

## Safety constraints (do not change in this stage)

- `EXPORT_REQUEST_INTERVAL_SECONDS`
- `EXPORT_MAX_API_REQUESTS_PER_DAY`
- `EXPORT_MAX_MEDIA_MB_PER_HOUR`
- `EXPORT_MAX_MEDIA_MB_PER_DAY`

## Stage 1/2 target values

```env
EXPORT_RUN_TIMEOUT_SECONDS=90
EXPORT_XS_CADENCE_MINUTES=2
EXPORT_S_CADENCE_MINUTES=4
EXPORT_M_CADENCE_MINUTES=8
EXPORT_L_CADENCE_MINUTES=20
EXPORT_L_WINDOW_START_HOUR=0
EXPORT_L_WINDOW_END_HOUR=12
```

Cron worker line target:

```cron
*/2 * * * * flock -n /var/lock/mydnaobv_export.lock timeout 120s nice -n 15 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once
```

## Apply on server

1. Backup runtime env:
```bash
cp /opt/mydnaobv/app/.env /opt/mydnaobv/app/.env.backup.stage12.$(date +%Y%m%d%H%M%S)
```

2. Edit the seven Stage 1/2 env values listed above.

3. Restart service:
```bash
systemctl restart mydnaobv
```

4. Check which crontab owns worker line:
```bash
crontab -l | grep mydnaobv_export.lock
sudo -u mydnaobv -H crontab -l | grep mydnaobv_export.lock
```

5. Update owner crontab to `*/2` and `timeout 120s`.

## Rollback values

```env
EXPORT_RUN_TIMEOUT_SECONDS=35
EXPORT_XS_CADENCE_MINUTES=5
EXPORT_S_CADENCE_MINUTES=10
EXPORT_M_CADENCE_MINUTES=20
EXPORT_L_CADENCE_MINUTES=60
EXPORT_L_WINDOW_START_HOUR=0
EXPORT_L_WINDOW_END_HOUR=6
```

Cron rollback line:

```cron
*/5 * * * * flock -n /var/lock/mydnaobv_export.lock timeout 45s nice -n 15 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once
```
