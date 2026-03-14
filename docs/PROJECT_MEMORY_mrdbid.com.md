# Project Memory - mrdbid.com

Purpose: continuity log for monetization and storage migration planning when direct repo access is unavailable.

## 2026-03-11
- Target direction confirmed:
  - move observation/user-upload image storage to Cloudflare R2
  - use direct browser uploads via presigned URLs
  - keep app servers for metadata/auth/business logic only
- Ad strategy confirmed:
  - ads on anonymous/public pages
  - never load AdSense script for authenticated sessions
- Next implementation entry required in mrdbid.com repo when accessible:
  - storage adapter + presign endpoint
  - background dominant-color extraction + cached DB field for sort
  - public/auth layout ad split

## 2026-03-12
- Cross-project rollout feedback from `myDNAobv` production run:
  - all-photo export modes can sharply increase queue runtime and resource pressure; start `mrdbid` rollout with conservative caps and explicit throttling.
  - generated file naming should be context-specific (avoid generic repeated names) for better user/download UX.
  - keep a manual deploy fallback path documented; CI deploy failures can produce noise even when live service is healthy.
- RubyMine handoff doc was revalidated and updated to explicitly call out quota-wait and split-output fallback as expected under heavy load.
