# AGENTS.md

## Mandatory Pre-Change Check
Before making any code, config, or deploy change in this repository, read:
- `docs/PROJECT_MEMORY.md` (especially the latest dated entry and "Critical Inclusion/Parity Invariants")

Do not proceed with implementation until those rules are explicitly satisfied by the planned change.

## Critical Rule Summary
- County/project inclusion logic must require DNA field `DNA Barcode ITS`.
- County output must remain constrained to the county/state scope.
- County guide output must include every observation in the index/list; if image export fails, include a placeholder page with explanation and iNaturalist link.
- Observation numbering in county guide pages must align with index numbering.
