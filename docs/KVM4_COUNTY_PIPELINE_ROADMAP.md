# KVM4 + County Pipeline Roadmap

Last updated: May 11, 2026

## Objective
Reduce large-export turnaround time and move to a curated, prebuilt county-product model that protects shared VPS resources.

## 2026-03-14 Sync Note
- After R2 cutover, KVM4 priority is compute/runtime headroom (CPU/RAM/concurrency), not image disk pressure.
- Keep treating `waiting_quota` and `partial_ready` as expected under heavy loads unless metrics/logs indicate true failure.
- Post-upgrade work should focus on Phase 4 parallel worker safety and incremental tuning with metric checkpoints.

## 2026-03-30 Sync Note
- Adopted explicit shared-VPS day/night operations profile for portfolio co-hosting:
  - daytime: single Python export worker lane with lower scheduling priority
  - night/rebuild window: dual Python export worker lanes for backlog throughput
  - Rails apps remain low-concurrency baseline to preserve UX floor
- Added guardrail table and rollback triggers in `docs/SHARED_VPS_DAY_NIGHT_RUNBOOK.md`.
- Operational policy: queue bulk/state rebuilds in night window; keep daytime focused on user-facing/smaller jobs.

## 2026-05-10 Sync Note
- Upgraded throughput profile for backlog recovery:
  - temporary backlog push mode: three export worker lanes every minute
  - steady-state mode retained as one daytime lane + two night lanes
  - cron commands now explicitly run from app directory with venv python path
- Throughput tuning defaults raised for upgraded VPS:
  - `EXPORT_RUN_TIMEOUT_SECONDS=300`
  - `EXPORT_DOWNLOAD_CHUNK_SIZE=16`
  - `EXPORT_DOWNLOAD_BYTE_BUDGET_MB=256`
  - `EXPORT_REQUEST_INTERVAL_SECONDS=0.5`
  - `EXPORT_XS/S/M/L_CADENCE_MINUTES=1/2/4/8`
  - `EXPORT_L_WINDOW_START_HOUR=0`, `EXPORT_L_WINDOW_END_HOUR=24`
- Large dataset packaging adjustments:
  - `EXPORT_ZIP_ONLY_PART_THRESHOLD=10`
  - `EXPORT_ZIP_CHUNK_SIZE_MB=1024`
  - new part-size cap knobs: `EXPORT_MAX_PART_SIZE_SINGLE_PHOTO`, `EXPORT_MAX_PART_SIZE_ALL_PHOTOS`

## 2026-05-11 Sync Note
- Stage 1 sync-throttling hardening started to reduce iNaturalist `429` loop pressure:
  - added global iNaturalist sync semaphore in export plan phase (`EXPORT_SYNC_MAX_CONCURRENT`, default `1`)
  - when sync slot is full, jobs remain in `plan` and retry with short delay (`EXPORT_SYNC_SLOT_RETRY_SECONDS`)
  - sync `429` retry now uses exponential backoff with jitter and bounded max:
    - `EXPORT_SYNC_BACKOFF_MAX_SECONDS`
    - `EXPORT_SYNC_BACKOFF_JITTER_RATIO`
- Scope intentionally limited to sync-pressure control (no county inclusion/parity behavior changes).

## 2026-05-11 Stage 2 Start Note
- Began decoupling sync from export execution for throttled runs:
  - when sync cannot proceed (slot contention or `429`) and cached observations already exist, configured product types can continue export from cached snapshot data.
  - new control: `EXPORT_SYNC_DEFER_TO_CACHE_PRODUCTS` (default `project`).
- Current scope:
  - project products may proceed from cache while sync is deferred.
  - county inclusion/parity logic unchanged; county invariants remain intact.

## 2026-05-11 Stage 3 Start Note
- Added auto-refresh retry damping for deferred-sync cache exports:
  - new control: `EXPORT_SYNC_DEFER_RETRY_MINUTES` (default `360`).
  - when latest completed list export is recent and list sync timestamp still predates that export start, auto-refresh skips immediate requeue until cooldown elapses, then retries force-sync normally.
- Purpose:
  - prevent continuous rebuild churn on stale `last_sync_at` while iNaturalist throttling persists.
  - keep sync retry pressure bounded without changing county inclusion/parity rules.

## Phase 1 (Now): KVM4 Readiness Without Architecture Rewrite
- Keep the current queue/worker model.
- Tune runtime throttles conservatively after KVM4 migration:
  - `EXPORT_DOWNLOAD_CHUNK_SIZE`
  - `EXPORT_DOWNLOAD_BYTE_BUDGET_MB`
  - `EXPORT_RUN_TIMEOUT_SECONDS`
  - `EXPORT_REQUEST_INTERVAL_SECONDS`
  - `EXPORT_*_CADENCE_MINUTES`
  - daily/hourly media and API request caps
- Re-check export timing and system metrics after each tuning step.
- Stage 1/2 baseline profile selected:
  - `EXPORT_RUN_TIMEOUT_SECONDS=90`
  - `EXPORT_XS/S/M/L_CADENCE_MINUTES=2/4/8/20`
  - `EXPORT_L_WINDOW_START_HOUR=0`, `EXPORT_L_WINDOW_END_HOUR=12`
  - worker cron cadence target: every 2 minutes (`timeout 120s`)

## Phase 2 (Implemented): Admin County Seeding by iNaturalist Project
- Add `inat_project_id` list filter in data model.
- Add admin flow to create one list per county for a selected US state:
  - inputs: `state_code`, `inat_project_id`
  - generated list fields include county place query + project filter
- Automatically queue county builds after seed for the selected state/project.

## Phase 3 (Implemented): Curated Downloads-Only Public Flow
- Public homepage now shows county download catalog (finished files).
- Public custom list creation flow is deprecated.
- Admin retains full county controls.
- Public catalog now shows split output downloads:
  - county guide file
  - observations index PDF
- Public rows display weekly refresh recency messaging.

## Phase 4 (In Progress): Full KVM4 Utilization
- Move from effectively single-lane worker behavior to safe parallel workers.
- Add queue locking/concurrency guardrails per list/job.
- Optionally isolate web and worker processes/services.
- Run day/night profile with metric gates; increase worker lanes only when p95 latency/error budgets remain healthy.

## Load Review Checklist
- Review `export_jobs` by hour/day:
  - count
  - `api_requests`
  - `bytes_downloaded`
  - queue latency and completion time
- Compare against VPS CPU/RAM/network and request logs.
- Confirm no sustained quota throttling bottlenecks unless intentional.
