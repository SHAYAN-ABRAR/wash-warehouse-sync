# Private state, versioning and audit

Everything lives under the state directory (`~/.wash_sync`, `WASH_SYNC_HOME` or
`--state-dir`). Keep it on an access-controlled disk; it contains warehouse copies.

```
manifest.json               current version pointer, schema/engine versions
warehouse_versions.json     immutable version records (see below)
source_fingerprints.jsonl   latest status per observation key (hash, status, version, plan)
facility_crosswalk.csv      VERIFIED_AUTO / APPROVED_MATCH / KEEP_SEPARATE links
change_history.jsonl        append-only events: INIT, LOAD_WAREHOUSE, PREVIEW, DECISION, APPROVE, APPLY, ROLLBACK, …
pending_conflicts.json      deferred / undecided review items carried between runs
settings.json               validated configuration
versions/                   WASH_Warehouse_v000.xlsx (baseline), v001, … (read-only)
plans/<plan>.json           immutable dry-run document
plans/<plan>.state.json     status, approval and decisions (small sidecar)
runs/<plan>/{preview,apply}/  audit sets
index/<sha>.v6.sqlite       rebuildable lookup cache (safe to delete)
jobs/  tmp/  uploads/       job journals, temporary output, uploaded exports
```

## Version record

Every version has a kind: `BASELINE` (the first warehouse loaded), `SYNC` (written by an
applied plan) or `LOADED` (a warehouse file loaded later, for example after QA corrections
made in Excel; it records the file name and the reason given). A version becomes current only
after its lookup index is complete, so every page shows either the previous version or the
fully prepared new one; if an index is missing (it is a cache), the pages report that the
warehouse is being prepared and refresh themselves while it is rebuilt in the background.

Each version records: creation time, previous version, plan, source files with
SHA-256, schema and engine versions, rows × columns, counts (added facilities,
updated facilities, new observations, fills, reviewed changes, unchanged, duplicates,
conflicts, deferred, rejected, invalid), new columns, the full validation report
including the preservation proof, and who approved and applied it.

If nothing changes, no version is created. Rollback only moves the current pointer;
later versions remain and can be restored. Optional retention (`backup_retention`)
deletes old non-current versions but always keeps the baseline and the current one.

## Audit set (per preview and per apply)

| File | Content |
|---|---|
| `sync_summary.json` | Counts by classification and source, version, validation |
| `record_changes.csv` | Every record: source file/row, IDs, match level/evidence, classification, proposed and executed action, decision, destination row, status |
| `matched_resources.csv` | Records matched to existing facilities |
| `new_facilities.csv` | Inserted facilities with placement explanation and destination row |
| `skipped_duplicates.csv` | Unchanged records and exact duplicates |
| `validation_errors.csv` | Every ERROR / WARNING / REVIEW issue with its reason |
| `conflicts.csv` | Review items, candidates, evidence, recommendation and decision |
| `field_change_history.csv` | Cell-level original value, new value and the exact source text |
| `report.html` | Human-readable report |

Personal-data columns are never read, so they cannot reach an audit. Only file names
(not paths) are recorded. CSV cells are protected against spreadsheet formula injection.

## Recovery

A crash or cancellation during apply leaves a job journal in `jobs/` and partial output
in `tmp/`. `wash-sync recover` marks the job as interrupted and removes the partial
output; the current verified version is never modified. Re-run `apply` for the plan.
