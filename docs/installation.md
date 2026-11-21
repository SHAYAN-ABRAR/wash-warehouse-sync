# Installation and first synchronization

## Requirements

* Python **3.12+** (Linux, Windows or macOS)
* Node.js **20+** — only to build the local web interface once
* About 1.5 GB free disk for the private state of a ~70 MB warehouse
  (versions, rebuildable index, audits). 4 GB RAM is plenty; the engine streams.

## Install

```bash
git clone https://github.com/SHAYAN-ABRAR/wash-warehouse-sync.git
cd wash-warehouse-sync
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[server]"            # add ",dev" for the test suite
npm --prefix apps/web ci
npm --prefix apps/web run build:local
```

Or run `./scripts/start-local.sh` (Windows: `scripts\start-local.ps1`), which does
all of the above on first use and then starts the app.

## Register the verified warehouse (once)

```bash
wash-sync init --warehouse /path/to/WASH_GPANI_WPMS_AQUA_BORELOG_1986_2026_QA_CORRECTED.xlsx
```

The workbook is verified (ZIP, XML, single worksheet, ordering, ranges), copied into
the private state directory as the read-only baseline `WASH_Warehouse_v000.xlsx`, and
indexed. The original file is never modified. Choose the location with
`--state-dir` or `WASH_SYNC_HOME` (default `~/.wash_sync`).

## Daily use

```bash
python -m wash_sync serve       # http://127.0.0.1:8765
```

1. **New Sync** — drop the latest export(s); check the detected source, column
   mapping, personal-data notice and survey period; run the dry run.
2. **Sync Preview** — counts per classification and source, new columns, every record
   with its evidence and exact cell changes; download the audit.
3. **Conflicts Center / Match Inspector** — decide each review item with a reason.
4. **Approve**, then **Apply** — the new version is written, verified and published.
5. **Sync History** — download `WASH_Warehouse_vNNN.xlsx`, inspect integrity checks,
   or make an earlier version current again.

## Command line equivalents

```bash
wash-sync inspect exports/survey_2027_dry.xlsx
wash-sync preview exports/survey_2027_dry.xlsx --period "2027 dry"
wash-sync review list <plan-id>
wash-sync review decide <plan-id> R00003 -d KEEP_SEPARATE -r "Different well at a shared project point"
wash-sync review bulk <plan-id> --class NEW_SOURCE_METADATA -d ACCEPT_NEW_VALUES -r "Status updates from WPMS"
wash-sync approve <plan-id>
wash-sync apply <plan-id>
wash-sync verify
wash-sync history
wash-sync rollback --to 1 -r "Revert the 2027 dry import"
wash-sync publish --confirm            # copy the current version to the configured private folder
```

## Configuration

`settings.json` in the state directory (editable in **Settings**). Example:
[`examples/config/settings.example.json`](../examples/config/settings.example.json).
Key options: validation mode (`standard`/`strict`), per-source completeness rules,
minimum automatic survey year, matching radii and depth tolerances, whether fully
verified cross-source matches apply automatically, approved location aliases, backup
retention, compression level and an optional private storage destination.
