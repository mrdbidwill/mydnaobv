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

## Routine Update Rule
On each major decision or architecture change:
1. Add one dated entry in this file.
2. Update `docs/KVM4_COUNTY_PIPELINE_ROADMAP.md` status/phase notes if scope changes.
3. Include migration IDs and operational impacts (performance, auth, public/admin behavior).
