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

## Routine Update Rule
On each major decision or architecture change:
1. Add one dated entry in this file.
2. Update `docs/KVM4_COUNTY_PIPELINE_ROADMAP.md` status/phase notes if scope changes.
3. Include migration IDs and operational impacts (performance, auth, public/admin behavior).
