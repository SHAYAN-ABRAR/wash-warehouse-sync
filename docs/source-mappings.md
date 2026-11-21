# Source mappings

Columns are resolved **by header name** (case, spacing and punctuation insensitive),
never by position. Aliases live in
[`packages/sync_core/wash_sync/resources/source_profiles.json`](../packages/sync_core/wash_sync/resources/source_profiles.json),
which the browser preview also imports. Columns listed under `personal_data_columns`
(caretaker names, phone numbers, staff names, e-mail accounts) are detected and
**never read**.

## AquaWatch

### Survey exports (`aquawatch_survey`)

| Source header | Warehouse field | Policy |
|---|---|---|
| Ground Water Resource | `aqua_gwc_id` | identity (Level 1) |
| Survey Year (`2026 (Wet Season)`, `2025`, `Dry 2027`) | selects the period columns | unknown season words are rejected |
| Arsenic (As), Iron (Fe), Chloride (Cl) | `aqua_{period}_{arsenic,iron,chloride}_raw` | compared measurement; source text preserved |
| Water Table (m) | `aqua_{period}_water_table_m` | compared measurement |
| (row number) | `aqua_{period}_source_row` | provenance, written only for a new period |
| Type of Tubewell, Depth of Tubewell, Latitude/Longitude, admin names | `aqua_well_type`, `aqua_well_depth_m`, `aqua_latitude_raw`, `aqua_longitude_raw`, `aqua_admin_names_raw` | fill-only facility attributes |

`{period}` is `{year}` or `{year}_{wet|dry}`. A new period creates five columns,
placed chronologically after the last AquaWatch period group (and before `borelog_*`).
The 2025 unsuffixed period and `2025_wet` are distinct. A new observation of an
existing well never creates a second facility row; a new well becomes
`AQUA{YEAR}{SEASON}:{id}`, inserted in its administrative group.

### Historical GWC table (`aquawatch_historical`)

The 1986–2017 columns (`aqua_1986` … `aqua_2017`) have no confirmed measurement
definition. Values are preserved exactly — numbers stay numbers and any non-numeric
text is kept verbatim — and are never reinterpreted as water-table depths. Identity
is GWC ID + well type + station coordinates + depth, because GWC IDs are reused for
paired and relocated stations.

## GPANI

Tubewell reports (`geo code`, `Water Point Depth`, `Water Quality Of …`, `W/P Type`)
and community-facility reports (`GEO Code`, `Lattitude`, `Longtitute`,
`Tank Capacity`). The geocode is a **location code, not a well identifier**: the
facility identity follows the warehouse convention `GP:{geocode}@{lat:.6f},{lon:.6f}`.
A geocode that reappears with different coordinates is an identity conflict for
review. Rows sharing an identity are collapsed (two-pass streaming group-by) into
`gpani_source_row_count`.

## WPMS

`GO Code` is the identity. Bangla division/district/upazila names are translated
only through mappings learned from verified warehouse rows (unambiguous, minimum
support). `Water Quality` free text (`Arsenic: 0.001 mg/L; Iron: 0.6 mg/L; …`) is
preserved in `wpms_water_quality_raw` and parsed into the `wpms_*` parameter columns
as derived values. A GO code equal to a GPANI geocode is a Level-2 candidate that
is merged only if administrative location, GPS and type all agree.

## Bore logs

Five tabs linked by `ID`: General Information, Well Fixtures, Water Quality,
Lithology & Well Description, Surface Water Information.

* One borehole = one record; layers are **not** flattened into facility rows.
* Lithology becomes a JSON profile on the borehole row (`borelog_lithology_profile_raw`)
  with interval checks (missing endpoints, non-positive thickness, gaps, overlaps).
  Source-row numbers inside the profile are provenance: reordering an export changes
  nothing. Appended layers are new observations; modified layers need review and the
  previous profile is preserved in `field_change_history.csv`.
* Profiles longer than Excel's 32,767-character cell limit are reported for review —
  never truncated.
* Depths such as `750 ft`, `46.02 m` and `৬৬০ ft` are converted; unitless depths are
  kept raw and flagged. Depths ≥ 1,000 m are flagged as possible test records.
* Swapped Latitude/Longitude columns are detected and corrected with a reason; raw
  values are kept in `borelog_latitude_raw` / `borelog_longitude_raw`.
* `TW ID No.` equal to a GPANI geocode is verified by admin location, GPS, tubewell
  type and depth before enriching the GPANI row (`MERGED_GPANI_UNIQUE_TW_ID_ADMIN_GPS_TYPE_DEPTH`).
