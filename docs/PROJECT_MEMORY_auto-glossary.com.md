# Project Memory - auto-glossary.com

Purpose: continuity log for monetization planning when direct repo access is unavailable.

## 2026-03-11
- Scope intentionally minimal:
  - AdSense integration only
  - no large image-storage migration needed for current usage
- Ad policy:
  - public pages may show ads
  - authenticated pages must never load AdSense script
- Next implementation entry required in auto-glossary.com repo when accessible:
  - add layout-level AdSense toggle by auth state
  - verify ad density and policy compliance for glossary pages

## 2026-03-12
- Reviewed after `myDNAobv` R2 production rollout.
- No scope change for `auto-glossary.com`: remain on AdSense-only implementation plan.
- RubyMine handoff doc revalidated; still no storage-migration scope added.

## 2026-03-30
- Added shared-host operating policy context:
  - `auto-glossary.com` should remain low fixed-concurrency on shared VPS.
  - off-peak CPU headroom is intentionally allocated to PDF rebuild pipeline in `myDNAobv`.
- This keeps glossary UX stable while enabling faster backlog completion for export-heavy workloads.
