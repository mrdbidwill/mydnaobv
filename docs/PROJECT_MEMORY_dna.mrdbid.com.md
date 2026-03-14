# Project Memory - dna.mrdbid.com

Purpose: portfolio-level continuity pointer for the `myDNAobv` codebase in this repository.

## 2026-03-11
- Implemented in-repo R2-ready publish backend (`EXPORT_PUBLISH_BACKEND=s3`) for export artifacts.
- Added one-time publish-dir backfill script to object storage.
- Added AdSense public-page gating so ads can be enabled on public pages while keeping auth/admin pages ad-free.
- Canonical implementation history remains in `docs/PROJECT_MEMORY.md`.

## 2026-03-12
- Production status:
  - DNS moved to Cloudflare and R2 custom-domain publishing validated.
  - New export artifacts are now being served from `downloads.dna.mrdbid.com`.
- Export UX update:
  - county/state-prefixed filenames implemented for generated PDFs/ZIPs to reduce ambiguity in downloads and browser tabs.
- Operational notes:
  - all-photo export mode (max 8) increases queue time and may trigger `partial_ready` on larger counties.
  - one permissions-related export failure confirmed need to keep export directory ownership aligned with service user.
  - server package mirror required compatible `boto3` fallback version to enable R2 publishing runtime.
