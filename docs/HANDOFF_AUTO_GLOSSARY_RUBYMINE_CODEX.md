# Handoff: `auto-glossary` RubyMine Codex Implementation Plan

Date: 2026-03-11  
Last reviewed: 2026-03-12
Owner context: portfolio-wide monetization rollout where `auto-glossary` is intentionally **AdSense-only** (no major image-storage migration required right now).

## Scope (Strict)
- Implement AdSense for public glossary/discovery pages.
- Never render AdSense script for authenticated users.
- Keep glossary UX clean and policy-compliant.

## Coordination Contract (Do Not Change)
1. Ads only on anonymous/public pages.
2. Authenticated users must never receive AdSense script markup.
3. Keep ad footprint light on utility/reference pages.
4. Integration must be fully flag-driven for rollback safety.

## Required Deliverables In `auto-glossary`

### 1) Config + Feature Flags
Add env-driven settings:
- `ADSENSE_ENABLED` (default false)
- `ADSENSE_CLIENT_ID`
- Optional slot IDs by placement region

Acceptance:
- Ad rendering can be disabled instantly via config.

### 2) Auth-State Ad Gating At Template/Layout Level
- Public layout: include AdSense script only when enabled and configured.
- Authenticated layout: never include AdSense script.
- On mixed routes, decide ad inclusion server-side by current auth state.

Acceptance:
- Logged-in page source has no AdSense script or slot markup.

### 3) Placement Rules For Glossary UX
Use light placements only:
- one inline placement after initial definition/content section
- optional footer placement on long public pages
- avoid placements that split definition headings from content blocks

Acceptance:
- Content remains easy to scan; no ad-heavy feeling.

### 4) Policy + Operations
- Ensure `ads.txt` is present and correct.
- Update privacy/cookie disclosures as needed.
- Prepare quick rollback runbook:
  - disable flag
  - clear caches
  - verify ad code removed

Acceptance:
- Policy and rollback documentation are present before full rollout.

### 5) Tests + Verification
Add/extend tests for:
- anon pages render ad code when enabled
- auth pages do not render ad code
- disabled/missing config renders no ad code

Manual checks:
1. Public glossary entry shows expected ad slot.
2. Logged-in pages show zero ad script markup.
3. Flag off removes ad code globally.

## Implementation Order (Recommended)
1. Config flags
2. Layout auth split
3. Minimal placements
4. Tests and manual verification
5. Policy + rollback docs

## Done Definition
Complete when all are true:
1. Public pages can show ads behind flags.
2. Authenticated pages never include AdSense script.
3. Readability and navigation remain clean.
4. Rollback path is documented and tested.

## Prompt To Give RubyMine Codex
"Implement `HANDOFF_AUTO_GLOSSARY_RUBYMINE_CODEX.md` exactly. Add feature-flagged AdSense integration for public pages, strict auth-state exclusion, minimal placements suited to glossary UX, and tests that prove gating behavior. Include rollback steps."

## Cross-Repo Note
This plan intentionally avoids storage-migration scope; it is monetization-only for now.

## 2026-03-12 Review Delta
- No scope change after `myDNAobv` production R2 rollout.
- Keep this implementation strictly AdSense-only unless image usage materially changes.
