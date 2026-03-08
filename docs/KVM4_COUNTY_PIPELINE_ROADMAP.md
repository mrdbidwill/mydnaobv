# KVM4 + County Pipeline Roadmap

Last updated: March 7, 2026

## Objective
Reduce large-export turnaround time and move to a curated, prebuilt county-product model that protects shared VPS resources.

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
