# Project Memory

Purpose: persistent decision/history log for future chat sessions and implementation continuity.

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

## Routine Update Rule
On each major decision or architecture change:
1. Add one dated entry in this file.
2. Update `docs/KVM4_COUNTY_PIPELINE_ROADMAP.md` status/phase notes if scope changes.
3. Include migration IDs and operational impacts (performance, auth, public/admin behavior).
