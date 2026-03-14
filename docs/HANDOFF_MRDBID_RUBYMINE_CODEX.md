# Handoff: `mrdbid` RubyMine Codex Implementation Plan

Date: 2026-03-11  
Last reviewed: 2026-03-12
Owner context: portfolio-wide R2 + ad-policy transition coordinated with `myDNAobv` updates already completed.

## Why This Handoff Exists
`myDNAobv` (dna.mrdbid.com side) already has:
- R2-ready S3 publish backend for artifacts
- AdSense public-only gating hooks
- transition docs and env model

This handoff is for the **`mrdbid` Rails codebase** so RubyMine Codex can implement its side in a coordinated way.

## Coordination Contract (Do Not Change)
1. Ads may appear only on public/anonymous pages.
2. Authenticated users must never receive AdSense script markup.
3. User/content images move to R2 with direct uploads (presigned), not streamed through app servers.
4. Image analysis (dominant color) must be async background work, never inline web request work.
5. Data model must support genus + color sorting for Fundis observation/PDF use cases.
6. Generated downloadable files must use context-specific names (not generic repeated names) for better user and browser-tab UX.

## Required Deliverables In `mrdbid`

### 1) Storage Foundation (R2)
Implement an object-storage adapter in Rails (S3-compatible for Cloudflare R2).
- Add env config (follow app conventions):
  - `R2_BUCKET`
  - `R2_ENDPOINT`
  - `R2_REGION` (default `auto`)
  - `R2_ACCESS_KEY_ID`
  - `R2_SECRET_ACCESS_KEY`
  - `R2_PUBLIC_BASE_URL` (or delivery domain)
- Use `aws-sdk-s3` with R2 endpoint and path-style addressing if required by app setup.
- Add startup validation for missing critical env in production.

Acceptance:
- App can upload/read/delete a test object in R2 from console/task.

### 2) Direct Upload Flow (Presigned)
Add authenticated endpoint(s) to generate presigned upload data for browser/mobile clients.
- Input: filename, content_type, byte_size, resource context (observation/photo).
- Validate MIME and size limits before issuing presign.
- Return object key + upload URL/fields + eventual public/delivery URL.
- Prevent key collisions (UUID/date prefix strategy).

Acceptance:
- New upload path avoids app-server file buffering.
- Large uploads no longer consume app disk significantly.

### 3) Persistence Model Updates
Add/extend DB fields for uploaded image metadata and sort features:
- storage key/url
- content type
- byte size
- width/height (if available)
- dominant color fields:
  - `dominant_hex` (string)
  - `dominant_hue` (0-359 int)
  - `color_bucket` (string, optional)

Acceptance:
- Records persist enough metadata for deterministic sorting/filtering.

### 4) Background Color Extraction
Add background job for dominant color extraction from first/cover image.
- Trigger after successful upload attach.
- Fetch thumbnail/small rendition from R2 (not full original when possible).
- Use `ruby-vips` preferred for memory efficiency.
- Persist hue/bucket fields.
- Retries + dead-letter/error state handling.

Acceptance:
- Color extraction does not run in request cycle.
- Failed color extraction does not block upload completion.

### 5) Genus + Color Sorting For Fundis PDFs
Implement query/service layer that supports:
- primary sort by genus
- secondary sort by dominant hue (or color bucket + hue)
- stable tiebreaker (observation id/date)

Acceptance:
- Fundis export path can produce deterministic genus/color grouped order.

### 6) AdSense Public-Only Gating
Implement layout-level script control:
- Public layout includes AdSense snippet only when enabled.
- Authenticated layout never includes AdSense script block.
- If same route can be both states, decide in server-rendered branch by auth status.

Acceptance:
- Page-source check confirms zero AdSense script for logged-in pages.

### 7) Policy/Operations Guardrails
- Add feature flags:
  - `ADSENSE_ENABLED`
  - `ADSENSE_CLIENT_ID`
  - optional slot IDs
- Add CSP updates if needed for AdSense domains.
- Add monitoring/logging around presign issuance, upload completion, and color job failures.

Acceptance:
- Rollout can be toggled without code rollback.

### 8) Artifact Naming For UX
Ensure generated files are uniquely identifiable from name alone.
- Prefer names like `<county-or-collection>_<type>.pdf` over generic `all_observations.pdf`.
- Keep naming deterministic and filesystem-safe (slugified).

Acceptance:
- Users can open multiple files/tabs and distinguish them without opening content.

### 9) Migration/Backfill Strategy
Create a one-time/backfill task to move existing local images to R2.
- Batch + resumable.
- Idempotent key mapping.
- Progress logging and restart safety.
- Optional verification pass (HEAD/list match).

Acceptance:
- Backfill can run safely in phases without downtime.

### 10) Tests Required
- service/unit tests for R2 client wrapper and presign policy.
- request tests for presign endpoint auth/validation.
- job tests for dominant-color extraction path.
- integration tests for ad-gating conditions (auth vs anon).

Acceptance:
- CI green with new tests and no regression in auth or uploads.

## Implementation Order (Recommended)
1. R2 adapter + env + smoke task
2. Presigned upload endpoint + client path
3. DB metadata fields
4. Background dominant-color job
5. Genus/color sorting integration in Fundis flow
6. Artifact naming conventions for generated outputs
7. AdSense gating
8. Backfill rake task + operational docs

## Done Definition
Project is considered complete when all are true:
1. New uploads are direct-to-R2.
2. Logged-in pages contain no AdSense script.
3. Fundis outputs can be sorted by genus then color.
4. Existing local-image migration path is documented and runnable.
5. Monitoring + rollback flags are in place.

## Prompt To Give RubyMine Codex
Use this exactly as kickoff prompt in the `mrdbid` repo:

"Implement the steps in `HANDOFF_MRDBID_RUBYMINE_CODEX.md` in order. Prioritize direct-to-R2 presigned uploads, async dominant-color extraction, genus+color sort support for Fundis outputs, and strict ad gating (public only; never for authenticated). Add migrations, tests, and operational docs. Keep changes production-safe with feature flags and idempotent backfill tasks."

## Cross-Repo Notes
- `myDNAobv` side is now live on R2 custom-domain publishing and public ad gating.
- `myDNAobv` rollout confirmed that all-photos modes substantially increase queue/runtime pressure; keep conservative caps and explicit throttling controls in initial `mrdbid` rollout.
- `myDNAobv` rollout also confirmed that backlog states like quota waiting and split-output fallback are normal under load; model these states as expected operational behavior in logs/UI.
- Keep a documented manual deploy fallback even after CI/CD is enabled, since pipeline failures can be noisy while production remains healthy.
- Reference file: `docs/R2_ADSENSE_TRANSITION_PLAN.md` in this repo.
