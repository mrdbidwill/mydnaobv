# Shared VPS Day/Night Operations Runbook

Date: May 10, 2026

Goal: maximize PDF build throughput on a shared VPS while preserving web responsiveness and maintainability.

Scope assumptions:
- Hostinger VPS on Ubuntu 24.04 LTS (upgraded class with additional CPU/RAM headroom).
- `myDNAobv` Python export pipeline is the dominant compute user.
- `mrdbid.com`, `auto-glossary.com`, and `mycowriter.com` Rails apps are low traffic and should stay responsive with small reserved capacity.

## Guardrail Targets

Use these as operating targets, not hard limits:

| Metric | Healthy target | Warning | Critical |
| --- | --- | --- | --- |
| CPU average (5m) | 35-60% | 60-75% | >85% for 10+ minutes |
| Load average (5m) | <60% of vCPU count | 60-85% of vCPU count | >100% of vCPU count sustained |
| Memory used | 50-70% | 70-80% | >85% or OOM |
| Swap I/O | near zero | sustained bursts | steady swap-in/out |
| Disk used | <70% | 70-85% | >90% |
| Disk iowait | <5% | 5-10% | >10% sustained |
| Web p95 latency | within SLO | +25% over baseline | +50% over baseline |
| 5xx error rate | <0.5% | 0.5-1.0% | >1.0% |
| Oldest queued export age | <1 minute | 1-5 minutes | >10 minutes |

## Operating Profiles

### Backlog Push Profile (temporary)

Use while oldest queued export age is stale.

- Python export workers:
  - run three lanes in parallel (separate flock lock files) every minute.
  - use `timeout 900s` to reduce finalize/zip interruption.
  - use moderate priority (`nice -n 6`, `ionice -c2 -n6`).
- Rails apps:
  - keep baseline small web concurrency while backlog push is active.

### Steady-State Profile

Use after backlog is current.

- Python export workers:
  - daytime: one lane (`timeout 600s`, lower priority).
  - night: two lanes (`timeout 900s`).
- Rails apps:
  - keep same baseline settings used during backlog push.

## Cron Template (Current Architecture)

Example schedule in `America/Chicago`:

```cron
# Backlog push profile (temporary): three lanes every minute.
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_a.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_b.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
* * * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_backlog_c.lock timeout 900s nice -n 6 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once

# Steady-state profile: enable after backlog is healthy.
# */2 7-22 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_day.lock timeout 600s nice -n 12 ionice -c2 -n7 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
# */2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_a.lock timeout 900s nice -n 8 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once
# */2 23,0-6 * * * cd /opt/mydnaobv/app && flock -n /var/lock/mydnaobv_export_night_b.lock timeout 900s nice -n 8 ionice -c2 -n6 /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --once

# Daily cleanup.
17 3 * * * cd /opt/mydnaobv/app && /opt/mydnaobv/app/.venv/bin/python -m app.exports.worker --cleanup
```

Why this is safe in current code:
- job selection uses DB row locking (`with_for_update(skip_locked=True)`), so concurrent workers claim different jobs.
- pick loop only claims `queued` / `waiting_quota`, and stale `running` jobs are auto-requeued.
- publish now runs outside finalize; long R2 uploads no longer block jobs from reaching `ready` / `partial_ready`.

## 2026-05-10 Operational Note

- Backlog profile now uses `900s` run slices to avoid repeated cutoff of large finalize/zip phases.
- `cd /opt/mydnaobv/app` plus venv python is required in cron to avoid module/import path issues.
- After queue age stabilizes, move back to steady-state profile.

## Queue Policy

- Prioritize user-facing builds during daytime by process policy:
  - queue single-county/single-project requests immediately.
  - schedule state-wide bulk rebuilds in night window.
- Keep stale-detector behavior enabled so unchanged lists do not create duplicate jobs.
- Keep iNaturalist API/media guardrail settings unchanged during throughput tuning.

## Optional Systemd Isolation (Recommended)

If/when moving worker execution from cron to dedicated systemd services/timers:

- Apply explicit limits to worker service:
  - `CPUQuota=70%` (day)
  - `CPUQuota=170%` (night)
  - `MemoryHigh=40%`
  - `MemoryMax=55%`
  - `TasksMax=256`
- Keep web services with guaranteed floor:
  - one web worker per Rails app minimum.
  - do not raise worker quotas if web p95 latency or 5xx error budget degrades.

## Rollback Triggers

Immediately drop to single-lane export mode and daytime nice/timeout values when any occurs:
- CPU >85% for 10+ minutes.
- load average exceeds total vCPU count for 10+ minutes.
- web p95 latency >50% above baseline for 10+ minutes.
- 5xx >1%.
- sustained swap activity.

## Review Cadence

- During initial PDF backfill/rebuild period: review daily.
- After backlog stabilizes: weekly review of queue age, completion time, and web p95/5xx.
- Re-tune in small steps only (one variable class at a time: lanes, timeout, cadence, then chunk/byte limits).
