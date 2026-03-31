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

## 2026-03-30
- Added shared-VPS utilization policy for `dna.mrdbid.com` in `docs/SHARED_VPS_DAY_NIGHT_RUNBOOK.md`:
  - day profile: single Python export worker lane with lower scheduling priority.
  - night/rebuild profile: dual Python export lanes (parallel `--once` runs with separate lock files).
- Guardrails explicitly documented (CPU/load/memory/swap/web p95/5xx/queue age) with rollback triggers.
- Queue policy clarified:
  - prioritize user-facing jobs in daytime.
  - run bulk/state rebuild queues primarily in low-traffic night windows.

## 2026-03-31
- Download-accessibility behavior clarified and deployed:
  - keep full ZIP/PDF artifacts available (no reduced-data fallback required).
  - public download rows now display file sizes and explicit large-download guidance for less technical users.
  - large ZIP artifacts are split into simpler chunk downloads (`Part 1`, `Part 2`, ...) when above configured threshold.
- Reliability behavior clarified and deployed:
  - sync-phase iNaturalist HTTP `429` now pauses to `waiting_quota` with retry timing, instead of terminal `failed`.
  - finalize no longer blocks on publish-to-R2; publish runs as a separate bounded worker pass.
  - ZIP packaging now avoids recompressing already-compressed payloads to reduce finalize CPU pressure.
- Current operations preference:
  - maintain all-photo/full-data outputs.
  - optimize delivery and completion reliability around that full-data requirement.
