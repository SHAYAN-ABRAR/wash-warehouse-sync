# Matching, change detection and safety rules

## Matching levels

1. **Exact source identity** — AquaWatch resource ID (historical: + type, station and
   depth), bore-log ID, WPMS GO code, GPANI geocode + source GPS. If the ID matches
   but the facility moved more than 5 km (or the division differs without GPS) and the
   content is not identical, the record is a *conflicting identity* (possible ID reuse).
2. **Verified cross-source identity** — reviewed crosswalk links, or a shared government
   code (bore-log TW ID / WPMS GO code = GPANI geocode) verified by administrative
   location, GPS distance (≤ `verify_gps_max_m`), tubewell type and depth tolerance.
   Any failed check turns it into a conflicting identity.
3. **Geographic candidate** — compatible facilities within `possible_match_radius_m`.
   Same village or identical coordinates are **never** proof of identity: the record
   becomes a *possible duplicate* and the reviewer chooses *approve match*, *keep
   separate*, *reject* or *defer*. "Keep separate" is remembered in the crosswalk.

Administrative names are compared with a punctuation/space-insensitive key plus the
validated alias table (CHITTAGONG ≈ CHATTOGRAM, BARISAL ≈ BARISHAL, BOGRA ≈ BOGURA,
COMILLA ≈ CUMILLA, JESSORE ≈ JASHORE, DAKSHIN SURMA ≈ DAKKHIN SURMA, plus a few
established district spellings). There is no fuzzy matching.

## Field policies

| Policy | Behaviour |
|---|---|
| identity | Written only when a row is created or enriched |
| compare | Blank target → filled; different existing value → review (never silent) |
| fill-only | Blank target → filled together with other changes; differences are reported only |
| derived | Follows its raw source field (e.g. `*_mg_l`, `*_depth_m`) |
| provenance | Source row/file; added for new groups, never overwritten |
| flags | Tokens appended, never removed (idempotent) |

Equality is semantic: `5.50 == 5.5`, `2023-08-28 == 2023-08-28 00:00:00`, and lithology
source-row numbers are ignored. The record fingerprint (SHA-256) covers identity and
compared content but not provenance, so re-ordered exports are not "new".

## Classifications

`NEW_FACILITY`, `NEW_OBSERVATION`, `FILLED_MISSING_VALUE` (applied after plan approval);
`UPDATED_MEASUREMENT`, `NEW_SOURCE_METADATA`, `POTENTIAL_DUPLICATE`, `CONFLICTING_IDENTITY`,
`CONFLICTING_MEASUREMENT`, `REQUIRES_REVIEW` (need a decision with a reason);
`UNCHANGED`, `EXACT_DUPLICATE` (skipped); `INVALID_SOURCE_RECORD` (rejected with reasons).

## Always-enforced guarantees

* Apply requires an approved plan (whose decisions have not changed since approval) or
  explicit authorization (`--yes`). Undecided items are deferred, never auto-approved.
* Plans are pinned to their base version's SHA-256; stale plans are refused.
* A FILL is applied only if the cell is still blank; overwriting needs a decided review option.
* Re-running the same files yields `UNCHANGED` everywhere and no new version.
* Rejected or "keep existing" decisions for identical content are remembered.
* The output is always exactly one worksheet; Excel's limits fail visibly.
* Writes go to a temporary file, are verified (including the preservation proof) and
  are published by atomic rename. Failures and cancellations leave the current version untouched.
