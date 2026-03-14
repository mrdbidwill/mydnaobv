# Handoff: `mycowriter` RubyMine Codex Implementation Plan

Date: 2026-03-11  
Last reviewed: 2026-03-12
Owner context: portfolio-wide monetization rollout where `mycowriter` is intentionally **AdSense-only** (no major image-storage migration required right now).

## Scope (Strict)
- Implement AdSense for public/anonymous pages.
- Never render AdSense script for authenticated users.
- Preserve existing UX/performance and policy compliance.

## Coordination Contract (Do Not Change)
1. Public pages may show ads.
2. Logged-in users must never receive AdSense script markup.
3. Ad integration must be feature-flagged for safe rollout/rollback.
4. Keep ad density moderate; avoid intrusive placement.

## Required Deliverables In `mycowriter`

### 1) Config + Feature Flags
Add env-based settings (follow app conventions):
- `ADSENSE_ENABLED` (default false)
- `ADSENSE_CLIENT_ID` (`ca-pub-...`)
- Optional slot IDs for specific placements (if app uses fixed slots)

Acceptance:
- Ads can be toggled off without code changes.

### 2) Layout-Level Ad Gating
Implement script injection at layout/template layer:
- Public layout includes AdSense script only when flag + client ID present.
- Authenticated layout excludes AdSense script entirely.
- If a route can render for both anon/auth states, branch server-side by auth state.

Acceptance:
- View source on authenticated pages contains no `adsbygoogle` or AdSense script include.

### 3) Safe Placement Strategy
Start with conservative placements:
- one top/between-content slot on public long-form pages
- no ads near form submit actions or primary workflow controls
- avoid ad clutter on thin-content pages

Acceptance:
- Public pages remain readable and functional with/without ads enabled.

### 4) Policy + Consent Readiness
- Ensure `ads.txt` exists and matches AdSense publisher ID.
- Add/update privacy and cookie disclosures if required by current site policy.
- If applicable for your traffic, wire consent management path before broad rollout.

Acceptance:
- Policy pages and disclosures are updated before full traffic enablement.

### 5) Performance Guardrails
- Lazy-load ad blocks where possible.
- Avoid blocking render-critical paths with ad code.
- Re-check Core Web Vitals before/after rollout.

Acceptance:
- No major regression in LCP/CLS from baseline.

### 6) Tests + Verification
Add/extend tests for:
- anonymous page contains ad markup when enabled
- authenticated page excludes ad markup regardless of flags
- flags missing/disabled => no ad script

Manual verification checklist:
1. Anonymous homepage/public article shows ad script/slot.
2. Logged-in dashboard/editor/profile pages show no ad script.
3. Disable flag and confirm no ad code anywhere.

## Implementation Order (Recommended)
1. Feature flags + config wiring
2. Layout auth split for script injection
3. Conservative placements
4. Tests + QA checklist
5. Policy/ads.txt/consent updates

## Done Definition
Complete when all are true:
1. Public pages can show ads behind flags.
2. Logged-in pages never include AdSense script markup.
3. No significant UX/CWV regression after enablement.
4. Operational docs updated with toggle/rollback instructions.

## Prompt To Give RubyMine Codex
"Implement `HANDOFF_MYCOWRITER_RUBYMINE_CODEX.md` exactly. Focus on feature-flagged AdSense integration for public pages only, strict no-ads for authenticated sessions, conservative placements, and tests proving auth-state gating. Include rollout/rollback notes."

## Cross-Repo Note
This plan is intentionally minimal because heavy image-storage migration is currently prioritized for `mrdbid` and `dna.mrdbid`.

## 2026-03-12 Review Delta
- No scope change after `myDNAobv` production R2 rollout.
- Keep this implementation strictly AdSense-only unless image usage materially changes.
