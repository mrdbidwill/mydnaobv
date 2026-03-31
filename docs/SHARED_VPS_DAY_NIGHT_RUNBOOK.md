# Shared VPS Day/Night Operations Runbook

Date: March 30, 2026

Goal: maximize PDF build throughput on a shared VPS while preserving web responsiveness and maintainability.

Scope assumptions:
- Hostinger VPS on Ubuntu 24.04 LTS (KVM2 class, 2 vCPU expected).
- `myDNAobv` Python export pipeline is the dominant compute user.
- `mrdbid.com`, `auto-glossary.com`, and `mycowriter.com` Rails apps are low traffic and should stay responsive with small reserved capacity.

## Guardrail Targets

Use these as operating targets, not hard limits:

| Metric | Healthy target | Warning | Critical |
| --- | --- | --- | --- |
| CPU average (5m) | 35-60% | 60-75% | >85% for 10+ minutes |
| Load average (5m, 2 vCPU host) | <1.2 | 1.2-1.8 | >2.0 sustained |
| Memory used | 50-70% | 70-80% | >85% or OOM |
| Swap I/O | near zero | sustained bursts | steady swap-in/out |
| Disk used | <70% | 70-85% | >90% |
| Disk iowait | <5% | 5-10% | >10% sustained |
| Web p95 latency | within SLO | +25% over baseline | +50% over baseline |
| 5xx error rate | <0.5% | 0.5-1.0% | >1.0% |
| Oldest queued export age | <1 minute | 1-5 minutes | >10 minutes |

## Operating Profiles

### Day Profile (UX-first, default)

Use for local daytime traffic window.

- Rails apps:
  - keep web concurrency intentionally small (for Puma: `WEB_CONCURRENCY=1`, `RAILS_MAX_THREADS=3`).
  - if background jobs are enabled in Rails repos, keep worker concurrency low (`SIDEKIQ_CONCURRENCY=2` starting point).
- Python export workers:
  - run one lane only.
  - keep lower priority (`nice -n 15`, `ionice -c2 -n7`).
  - keep per-run slice moderate (`timeout 300s`).

### Night/Rebuild Profile (throughput-first)

Use for low-traffic windows and initial/major backlog builds.

- Rails apps:
  - keep same small baseline to preserve login/admin responsiveness.
- Python export workers:
  - run two lanes in parallel (separate flock lock files).
  - reduce nice penalty (`nice -n 8`, same `ionice -c2 -n7`).
  - allow longer per-run slice (`timeout 600s`).
  - queue bulk rebuild actions in this window.

## Cron Template (Current Architecture)

Example schedule in `America/Chicago`:

```cron
# Day profile: single export lane during daytime.
*/2 7-22 * * * flock -n /var/lock/mydnaobv_export_day.lock timeout 300s nice -n 15 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once

# Night profile: dual export lanes for backlog/rebuild throughput.
*/2 23,0-6 * * * flock -n /var/lock/mydnaobv_export_night_a.lock timeout 600s nice -n 8 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once
*/2 23,0-6 * * * flock -n /var/lock/mydnaobv_export_night_b.lock timeout 600s nice -n 8 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once

# Daily cleanup.
17 3 * * * /usr/bin/python3 -m app.exports.worker --cleanup
```

Why this is safe in current code:
- job selection uses DB row locking (`with_for_update(skip_locked=True)`), so concurrent workers claim different jobs.
- pick loop only claims `queued` / `waiting_quota`, and stale `running` jobs are auto-requeued.
- publish now runs outside finalize; long R2 uploads no longer block jobs from reaching `ready` / `partial_ready`.

## 2026-03-31 Operational Note

- Timeouts were raised from `120/150s` to `300/600s` (day/night) after observing large finalize/zip phases for all-photo jobs.
- Keep this higher timeout profile unless guardrail metrics regress; short external timeouts can force stale-lock recovery loops on large jobs.

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
- load average >2.0 sustained on 2 vCPU host.
- web p95 latency >50% above baseline for 10+ minutes.
- 5xx >1%.
- sustained swap activity.

## Review Cadence

- During initial PDF backfill/rebuild period: review daily.
- After backlog stabilizes: weekly review of queue age, completion time, and web p95/5xx.
- Re-tune in small steps only (one variable class at a time: lanes, timeout, cadence, then chunk/byte limits).
