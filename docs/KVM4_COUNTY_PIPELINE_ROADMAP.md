# KVM4 + County Pipeline Roadmap

Last updated: March 14, 2026

## Objective
Reduce large-export turnaround time and move to a curated, prebuilt county-product model that protects shared VPS resources.

## 2026-03-14 Sync Note
- After R2 cutover, KVM4 priority is compute/runtime headroom (CPU/RAM/concurrency), not image disk pressure.
- Keep treating `waiting_quota` and `partial_ready` as expected under heavy loads unless metrics/logs indicate true failure.
- Post-upgrade work should focus on Phase 4 parallel worker safety and incremental tuning with metric checkpoints.

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

## Load Review Checklist
- Review `export_jobs` by hour/day:
  - count
  - `api_requests`
  - `bytes_downloaded`
  - queue latency and completion time
- Compare against VPS CPU/RAM/network and request logs.
- Confirm no sustained quota throttling bottlenecks unless intentional.
