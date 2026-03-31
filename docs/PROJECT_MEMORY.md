# Project Memory

Purpose: persistent decision/history log for future chat sessions and implementation continuity.

## Critical Inclusion/Parity Invariants (Must Read Before Changes)
- Every county/public output must use this inclusion rule:
  - observation is inside the county/state scope
  - observation belongs to one of configured county project IDs (currently four AMS sequencing projects)
  - observation has `DNA Barcode ITS`
- The county guide output must remain observation-complete relative to the observation index/list:
  - if an observation is in the list/index, it must appear in county output
  - if image export fails/unavailable, include a placeholder page with a clear explanation and the iNaturalist URL
- County guide observation numbering must align with index numbering (`1..N` by the same sorted observation list).
- Before any implementation/deploy change, re-check this file and verify changes preserve these invariants.

## 2026-03-07
- Confirmed roadmap direction:
  - continue current plan now
  - prepare/tune for Hostinger KVM4 migration
  - later implement architecture changes for full multi-vCPU usage
- Implemented `inat_project_id` support on observation lists.
- Implemented admin-only county seeding workflow by state + iNaturalist project.
- Added US state selector and county generation via Census county dataset endpoint.
- Enabled iNaturalist observation fetch/estimate filters to run with:
  - user ID/login filters
  - project ID/slug filters
  - optional place filters
- Added migration for `observation_lists.inat_project_id`.
- Added this memory file and roadmap doc to keep progress discoverable.
- Fixed a production issue where invalid/nonexistent project slugs returned iNaturalist `422` on sync.
  - Added canonical project resolution against iNaturalist (`/projects/{id_or_slug}` / autocomplete fallback).
  - Admin county seeding now validates project IDs/slugs before creating county lists.
  - Sync/estimate now return clear project-not-found validation errors instead of raw `422` URLs.
- Added pagination UX updates after county-scale seeding:
  - Admin saved-lists view now paginates.
  - Admin and Export Center both include a direct "Go to page" control.
- Homepage (`/`) saved-lists section now includes:
  - top + bottom pagination controls with "Go to page"
  - sortable order (`Title A to Z` / `Newest first`) to find county lists faster
- Flow pivot implemented:
  - Public `/` now serves finished county downloads catalog.
  - Admin `/admin` now acts as county build dashboard (seed, state build, per-county sync/rebuild/show-hide/delete).
  - Public custom list-creation endpoint is deprecated.
- Added background build safety improvement:
  - export jobs now support `force_sync` so queue builds can sync observations before rendering PDFs.

## 2026-03-08
- Implemented split county outputs per completed export job:
  - `observations_index.pdf` (DNA-confirmed list + iNaturalist links)
  - existing county guide output (`merged_pdf`, ZIP fallback for large jobs)
- Public county catalog now surfaces two distinct download buttons:
  - Observation PDF
  - County file (PDF/ZIP depending on build size)
- Added public weekly refresh messaging:
  - per-county "last refreshed" and "next refresh target"
  - configurable cadence via `PUBLIC_REFRESH_INTERVAL_DAYS` (default `7`)
- Clarified offline behavior:
  - PDF content is offline-friendly
  - external iNaturalist links still require internet access
- Admin UX wording updated to "Process state" for state-wide sync+build queue action.
- Added Stage 1/2 throughput tuning profile docs for production rollout:
  - higher per-run timeout and faster queue cadences
  - wider L-job processing window
  - worker cron moved to every 2 minutes with `timeout 120s`
  - iNaturalist guardrails intentionally unchanged
- Added runbook: `docs/STAGE12_TUNING_RUNBOOK.md` with apply/rollback commands.
- Backlog for next commit:
  - add admin-protected "Reset all county products" action with strong confirmation
- Fixed SQLAlchemy relationship mapping issue causing worker warning:
  - `Observation.photos` / export relationship collections were being interpreted as scalar (`uselist=False`)
  - normalized explicit collection relationship declarations to enforce list semantics
  - warning addressed: `SAWarning: Multiple rows returned with uselist=False for ... Observation.photos`
- Added homepage AMS-facing project context section:
  - purpose/field-use notes
  - split output explanation (observation list PDF + county PDF)
  - explicit current inclusion rule: project membership AND `DNA Barcode ITS`
  - expandable note listing future filtering/data-source questions under review

## 2026-03-11
- Implemented object-storage publish backend for exports with S3-compatible support (Cloudflare R2 ready):
  - New config: `EXPORT_PUBLISH_BACKEND` (`filesystem` or `s3`).
  - New S3/R2 config: `EXPORT_PUBLISH_BUCKET`, `EXPORT_PUBLISH_PREFIX`, `EXPORT_PUBLISH_S3_ENDPOINT`, `EXPORT_PUBLISH_S3_REGION`, `EXPORT_PUBLISH_S3_ACCESS_KEY_ID`, `EXPORT_PUBLISH_S3_SECRET_ACCESS_KEY`.
  - Export publisher now uploads `job` and `latest` artifacts directly to object storage when backend is `s3`.
  - Added one-time migration helper: `scripts/sync_publish_dir_to_object_storage.py`.
- Implemented AdSense public-page gating to support monetization while keeping authenticated/admin pages ad-free:
  - New config: `ADSENSE_ENABLED`, `ADSENSE_CLIENT_ID`, `ADSENSE_BANNER_SLOT`.
  - Added template response helper to explicitly control ad rendering per route.
  - Public homepage (`/`) can render ad script/banner when enabled.
  - Admin/authenticated pages do not include ad script.
- Documentation updates:
  - `.env.example` + `README.md` now include R2/S3 publish and AdSense setup examples.
  - Added cross-project continuity docs for `mrdbid.com`, `mycowriter.com`, and `auto-glossary.com` planning from this repo until direct codebase access is available.
  - Added transfer-ready RubyMine handoff doc for `mrdbid` implementation coordination:
    - `docs/HANDOFF_MRDBID_RUBYMINE_CODEX.md`
  - Added transfer-ready RubyMine handoff docs for AdSense-only rollouts:
    - `docs/HANDOFF_MYCOWRITER_RUBYMINE_CODEX.md`
    - `docs/HANDOFF_AUTO_GLOSSARY_RUBYMINE_CODEX.md`

## 2026-03-12
- Completed production Cloudflare cutover for `mrdbid.com` DNS and R2 integration for `myDNAobv` published artifacts:
  - R2 bucket + custom domain `downloads.dna.mrdbid.com` verified.
  - Server export publish backend (`EXPORT_PUBLISH_BACKEND=s3`) validated with real county jobs.
  - Public latest URLs now resolve from R2 custom-domain path.
- Added county/state-prefixed export artifact filenames to improve user/browser-tab differentiation:
  - `*_all_observations.pdf`
  - `*_observations_index.pdf`
  - `*_observation_export_parts.zip`
- Operational findings from production backlog run:
  - enabling all-photos mode (`EXPORT_INCLUDE_ALL_PHOTOS=true`, max 8) materially increases queue time and resource pressure.
  - larger counties may land in `partial_ready` (split ZIP fallback) by design.
  - `waiting_quota`/future `next_run_at` states are expected pacing behavior, not necessarily failures.
  - one failed job (`permission denied` under export directory) confirmed need to keep export directory ownership aligned with service user.
- Deploy/runtime note:
  - R2 publish initially failed on server due missing `boto3`.
  - package mirror did not provide `boto3==1.38.49`; installed compatible `1.38.46` and publish succeeded.
  - GitHub Actions deploy emails can fail independently from manual server deploy; manual deploy remains reliable path until Actions settings are tuned.
- Cross-repo handoff docs reviewed and aligned with production learnings:
  - `mrdbid` handoff now explicitly captures quota-wait/split-output-as-normal behavior and manual deploy fallback guidance.
  - `mycowriter` + `auto-glossary` handoffs reconfirmed as AdSense-only scope (no storage migration change).

## 2026-03-14
- Post-R2 capacity decision clarified:
  - Hostinger KVM4 value is now primarily throughput/stability headroom (CPU/RAM/concurrency), not image disk relief.
  - R2 offload removed most storage-growth pressure from the VPS, but heavy county rebuilds still stress compute/runtime.
- Expected behavior reminders:
  - `waiting_quota` states can remain normal when pacing/quotas are active.
  - `partial_ready` for large counties is an expected split-output fallback, not automatically a failure.
- Maintenance posture (current):
  - keep manual deploy path as known-good fallback while GitHub Actions deploy workflow is noisy/failing.
  - keep service-user ownership/permissions aligned for export working directories.
  - avoid running git operations as `root` inside `/opt/mydnaobv/app`; mixed ownership in `.git/objects` breaks both manual and CI deploy fetch/pull.
  - keep conservative photo/export limits until backlog and runtime metrics are consistently healthy.
  - keep `dna.mrdbid.com` as Cloudflare `DNS only` (gray cloud) because proxied mode breaks SSH/admin access to port 22.
  - ensure operator SSH public key remains present in `/opt/mydnaobv/.ssh/authorized_keys` (the account home for `mydnaobv`).
  - keep limited passwordless sudo for deploy user on `systemctl restart/status mydnaobv` so non-interactive deploy scripts do not fail on sudo prompts.
- Future improvements after any VPS plan upgrade:
  - execute Phase 4 work for safe parallel workers and queue locking.
  - retune cadence/chunk/timeout settings incrementally with metric checks after each change.
  - separate web and worker process capacity if sustained concurrency increases.

## 2026-03-26
- Taxonomy credibility update for DNA-driven reevaluation:
  - Added explicit separation of `observation_taxon` (observer-side identification) and `community_taxon` in cached observation data.
  - Sync logic now derives observer-side taxon from iNaturalist `identifications` (prefers observer current identification) instead of relying only on aggregate taxon fields.
  - PDF export ordering now prioritizes genus from `observation_taxon` so outputs reflect post-DNA reevaluation intent.
  - PDF pages/index now print `community_taxon` per observation for transparent side-by-side review.
- Data model/migration update:
  - `observations`: added `observation_taxon_id/name/rank` and `community_taxon_id/name/rank`.
  - `export_items`: added `observation_taxon_name` and `community_taxon_name` for render-time labeling.
  - Added migration `f7c1e2d3a4b5_add_observation_vs_community_taxon_fields.py`.

## 2026-03-27
- Added configurable sort-source toggle for genus ordering in export jobs:
  - New env var: `EXPORT_SORT_TAXON_SOURCE` (`observation` default, or `taxon`).
  - `observation`: uses observer-side taxon for ordering.
  - `taxon`: uses iNaturalist current taxon (`taxon`) for ordering.
- iNaturalist sync mapping updated:
  - `observations.taxon_name` now stores iNaturalist current taxon (`taxon`) instead of community fallback.
  - Separate `observation_taxon_*` and `community_taxon_*` remain unchanged for auditability.
- Observation index PDF labeling expanded:
  - now prints `iNaturalist taxon`, `Observation taxon`, and `Community taxon` per row.
- Added per-export genera summary artifact for both county and project lists:
  - new file: `*_genera_count.txt`
  - contents are numbered alphabetical genus-style tokens with observation counts (e.g., `1. Agaricales (4)`).
  - included in ZIP output and public download routing.

## 2026-03-28
- County sync source strategy updated for sequencing workflows:
  - County list sync now queries all projects in `INAT_COUNTY_PROJECT_IDS` (default `124358,184305,132913,251751`) instead of relying on a single project ID.
  - Observations are de-duplicated by iNaturalist `observation.id` across those project queries before caching/export.
  - `DNA Barcode ITS` remains required; no API/media throttle settings changed.

## 2026-03-29
- Validation incident and rule hardening:
  - User reported apparent mismatch between Baldwin observation index and county guide pages.
  - Confirmed need for strict parity guarantees independent of image download outcomes.
- Required behavior clarified and adopted:
  - county guide must include every listed observation even when all images fail/unavailable
  - county guide pages must carry index-aligned observation numbering for easier verification
  - image-missing pages must explain that iNaturalist may still contain images but this build could not download/render them
- Operational requirement added:
  - overdue public county refreshes must be auto-queued by worker loop (within existing throttle/queue controls)
  - stale "Refresh due" rows should no longer depend solely on manual admin enqueue actions
- CDN cache consistency hardening:
  - `published_latest_url(...)` now appends a stable artifact version query token (`?v=<artifact_id>`) to avoid stale `latest` links during cache propagation.
  - S3/R2 publish now sets cache policy metadata:
    - immutable cache for `job_*/` artifacts
    - revalidate/no-cache policy for `latest/` artifacts and latest manifest
- Added iNaturalist observation field `20740` support (`Barcode Inferred Species or Name`) for county/project outputs:
  - synced into cached observations
  - copied into export items
  - rendered on county guide PDFs and observation index PDFs with fallback `No set`
  - exposed in admin list observation table for parity checks
- Added export image cache hardening and scheduled maintenance:
  - county/project image download phase now reuses cached media when available, with periodic TTL revalidation from source
  - when refresh fetch fails or quotas are hit, stale cached image can be used to keep observation pages renderable
  - worker `--once` now runs interval-based maintenance: expired export cleanup + image cache prune
  - new cache controls: `EXPORT_IMAGE_CACHE_*` (enable, TTL days, retention days, prune interval, max prune files/run)
- Public auto-refresh queue scope expanded:
  - due-job enqueue now covers both public `county` and public `project` products (force-sync rebuild path unchanged)
- Worker concurrency/lock hardening:
  - pick loop now only claims `queued` / `waiting_quota` jobs; `running` jobs are not pickable by parallel workers
  - stale `running` jobs are auto-requeued after timeout-based heartbeat cutoff
  - worker now marks claimed jobs `running` immediately and returns unfinished work to `queued` for the next cycle
  - process exception path now does session rollback before fail-mark commit (prevents `PendingRollbackError` cascades)

## 2026-03-30
- Capacity strategy finalized for shared Hostinger VPS (Ubuntu 24.04, KVM2 class) with mixed workloads:
  - Rails portfolio apps (`mrdbid.com`, `auto-glossary.com`, `mycowriter.com`) remain low-concurrency baseline.
  - `myDNAobv` Python export pipeline receives priority for initial/major rebuild throughput, especially in low-traffic windows.
- Added shared operations runbook:
  - `docs/SHARED_VPS_DAY_NIGHT_RUNBOOK.md`
  - includes guardrail targets (CPU/load/memory/swap/latency/error/queue age), rollback triggers, and review cadence.
- Day/night execution policy documented:
  - daytime: single export worker lane (`--once`) with lower CPU scheduling priority.
  - night/rebuild window: dual export worker lanes (separate lock files) for higher backlog throughput.
  - bulk/state rebuilds should be queued in night window; daytime focuses on user-facing jobs.
- Maintains existing iNaturalist guardrails and existing job-staleness de-dup behavior.
- No change to county inclusion/parity invariants; county output logic and numbering guarantees remain required exactly as listed above.
- Added timezone-specific cron template reference for operators:
  - `docs/CRONTAB_DAY_NIGHT_AMERICA_CHICAGO.md`
  - includes ready-to-paste blocks for either `root` or `mydnaobv` crontab ownership with `CRON_TZ=America/Chicago`.

## 2026-03-31
- Production incident findings for project builds:
  - `Job #435` (`ams-fungal-diversity-project-collection`) failed in plan/sync phase due iNaturalist HTTP `429 normal_throttling` during force-sync (`/observations?page=2`).
  - Existing public "Ready" files remained available because public links use latest completed (`ready`/`partial_ready`) artifacts, not latest attempted job.
- Queue reliability hardening implemented:
  - sync-phase iNaturalist HTTP `429` now maps to `waiting_quota` with delayed retry instead of terminal `failed`.
  - retry delay now respects `Retry-After` header when present (bounded) with safe default delay when missing.
  - `_schedule_next_run(...)` now preserves pre-set future `next_run_at` for `waiting_quota` jobs instead of overriding with generic cadence.
- Test coverage added:
  - sync `429` transitions to retriable wait state.
  - `waiting_quota` jobs retain explicit retry timestamps.
- Operational note:
  - very large finalize/zip work can exceed external cron `timeout` windows and leave jobs cycling via stale-lock recovery; treat as timeout-pressure tuning issue (not parity logic failure) and adjust runtime windows conservatively.
- Download UX + large artifact reliability update:
  - public county/project download rows now display file size labels and plain-language large-download guidance.
  - large ZIP artifacts can now auto-split into sequential public chunk files (`*.part001`, `*.part002`, ...) when above configurable threshold (`EXPORT_ZIP_CHUNK_SIZE_MB`).
  - public artifact route now allows `zip_chunk` downloads and can redirect to published latest URL if local retained file is missing.
  - policy intent: keep full-data artifacts available while improving accessibility for low-skill or low-bandwidth users (guidance + part downloads, not data reduction).
- Finalize/runtime throughput update:
  - ZIP assembly now stores already-compressed file types (PDF/ZIP/media) with `ZIP_STORED` to reduce CPU pressure during large package generation.
- Publish decoupling implemented:
  - export finalize no longer blocks on R2/filesystem publish; jobs can complete as `ready` / `partial_ready` first.
  - worker loop now runs a bounded publish pass (`EXPORT_PUBLISH_JOBS_PER_RUN`) outside finalize.
  - S3 latest-link availability is now gated by local publish-state marker (instead of assuming latest exists), avoiding stale/nonexistent `latest` links for newly completed but not-yet-published artifacts.
  - publish success records per-list latest published job marker under export storage.

## 2026-03-31
- Public homepage UX and artifact-link consistency update:
  - increased large-screen content container width target to ~60% viewport for improved readability on desktop.
  - shortened public project display titles by removing redundant `— iNaturalist Project ...` suffix in project rows.
  - updated genera action wording from `Download Genera Count` to `Genera Count` and clarified it opens a text listing of genera with observation counts.
- ZIP chunk reliability hardening:
  - public part-download buttons now only render when the specific chunk artifact is available (local file or confirmed published file).
  - chunk links prefer app-routed download endpoints, avoiding direct `latest/` object URLs that may not exist for chunk artifacts in older publish states.
  - S3/R2 latest-availability check now supports filename-level validation via publish-state `latest_filenames`, preventing false-positive latest links for missing artifacts.
  - backward compatibility: when legacy publish-state lacks filename list, `zip_chunk` is treated as unavailable for direct latest linking to avoid user-facing 404s.
- Export filename simplification for project products:
  - project artifact filename prefixes now use the cleaned project title (or project ID fallback) without duplicating the `iNaturalist Project` suffix pattern.
- County inclusion/parity invariants unchanged:
  - required county scope + project membership + `DNA Barcode ITS` rule preserved.
  - county guide observation-completeness and numbering alignment behavior unchanged.

## Routine Update Rule
On each major decision or architecture change:
1. Add one dated entry in this file.
2. Update `docs/KVM4_COUNTY_PIPELINE_ROADMAP.md` status/phase notes if scope changes.
3. Include migration IDs and operational impacts (performance, auth, public/admin behavior).
