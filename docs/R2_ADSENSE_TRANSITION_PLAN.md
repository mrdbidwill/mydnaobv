# R2 + AdSense Transition Plan (Portfolio)

Date: 2026-03-11

## Goal
- Remove VPS disk/IO pressure by moving large image/export artifacts to object storage.
- Enable monetization on public pages only.
- Keep authenticated experiences ad-free.

## What Is Already Implemented In This Repo (`dna.mrdbid.com` / `myDNAobv`)
- S3-compatible publish backend for export artifacts (Cloudflare R2 ready).
- New env-driven backend selector: `EXPORT_PUBLISH_BACKEND=filesystem|s3`.
- New env vars for S3/R2 object storage publishing.
- One-time local publish dir sync script: `scripts/sync_publish_dir_to_object_storage.py`.
- AdSense script/banner gating at template layer:
  - public template responses can show ads
  - admin/authenticated template responses do not load AdSense.

## Cloudflare R2 Account Setup Details (Specific)
1. Create R2 bucket per product environment.
   - Example: `dna-mrdbid-downloads-prod`, `mrdbid-images-prod`.
2. Create API token scoped to bucket(s) only.
   - Permissions needed here: read/write/delete for publish paths.
3. Record S3 endpoint format:
   - `https://<accountid>.r2.cloudflarestorage.com`
4. Decide URL strategy before migration.
   - Preferred: custom domain (for stable public URLs).
5. Configure lifecycle rules.
   - Keep `latest/` indefinitely.
   - Expire old `job_*/` prefixes after retention window if desired.
6. Configure CORS only where browser direct-upload is used.
   - For presigned browser upload flows in other projects, allow your exact app origins and methods (`PUT`, `GET`, `HEAD`).
7. Set billing alerts before go-live.
   - Alert on storage growth and request spikes.

## Recommended Execution Order
1. `dna.mrdbid.com` (this repo): switch publish backend to R2 first.
2. `mrdbid.com`: migrate observation image originals/derivatives to R2 with direct browser uploads.
3. `mycowriter.com` and `auto-glossary.com`: AdSense-only rollout (minimal image migration required).
4. After one full cycle, tighten lifecycle/retention for stale objects.

## `dna.mrdbid.com` Cutover Steps
1. Install dependencies and deploy code.
   - `pip install -r requirements-dev.txt`
2. Set env values:
   - `EXPORT_PUBLISH_ENABLED=true`
   - `EXPORT_PUBLISH_BACKEND=s3`
   - `EXPORT_PUBLISH_BUCKET=<bucket>`
   - `EXPORT_PUBLISH_PREFIX=mydnaobv`
   - `EXPORT_PUBLISH_S3_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com`
   - `EXPORT_PUBLISH_S3_REGION=auto`
   - `EXPORT_PUBLISH_S3_ACCESS_KEY_ID=<key>`
   - `EXPORT_PUBLISH_S3_SECRET_ACCESS_KEY=<secret>`
   - `EXPORT_PUBLISH_BASE_URL=https://downloads.dna.mrdbid.com/mydnaobv`
3. Keep `EXPORT_STORAGE_DIR` on fast ephemeral disk (`/tmp/...`) and `EXPORT_RETENTION_HOURS` low.
4. Run one job and verify:
   - manifest and PDFs exist under `list_<id>/latest/` in R2.
   - homepage links resolve from public base URL.
5. Backfill historical published files (optional):
   - dry-run: `python scripts/sync_publish_dir_to_object_storage.py`
   - apply: `python scripts/sync_publish_dir_to_object_storage.py --apply`
6. Enable AdSense on public pages:
   - `ADSENSE_ENABLED=true`
   - `ADSENSE_CLIENT_ID=ca-pub-...`
   - optional `ADSENSE_BANNER_SLOT=<slot>`
7. Re-deploy and verify no ad script loads on `/admin` or any auth-protected page.

## Cross-Project Ad Policy Rule (Use Everywhere)
- Render AdSense script only in public layout/template.
- Authenticated layout/template must never include AdSense script.
- Do not rely only on URL exclusion for auth states that share routes.

## Status Tracker
- `dna.mrdbid.com` code changes: complete in this repo.
- `mrdbid.com`: pending (repo access required).
- `mycowriter.com`: pending (repo access required).
- `auto-glossary.com`: pending (repo access required).
