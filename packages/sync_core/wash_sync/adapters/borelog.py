"""Bore-log adapter: five linked tabs keyed by the bore-log ``ID``.

One borehole becomes one record. Geological layers are *not* flattened into
facility rows: the lithology tab is aggregated into a JSON profile on the
borehole's row (``borelog_lithology_profile_raw``), and multiple surface-water
observations are preserved in ``borelog_surface_observations_raw``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from ..chemistry import parse_measurement
from ..geo import resolve_coordinates
from ..numbers import parse_length
from ..records import Category, FieldValue, Policy, SourceRecord
from ..schema import EXCEL_MAX_CELL_CHARS
from ..text import clean, collapse_ws, norm_code, norm_header, norm_id, to_text
from .base import AdapterContext, _known_headers, normalize_well_type
from .tabular import HeaderMap, list_sheets, read_table

TEST_DEPTH_M = 1000.0
UNREASONABLE_DEPTH_M = 600.0
LITHO_KEYS = ("from", "to", "color", "color_code", "lithology", "lithology_code", "remarks")
SURFACE_KEYS = ("river", "dry_flow", "quality_opinion", "dry_depth_ft", "dry_quality")


def _raw_text(value: Any) -> str:
    """Raw JSON profile values keep the source text (e.g. ``N/A``); only spacing is normalized."""
    text = to_text(value)
    return collapse_ws(text) if text else ""


def _load_tab(path: str, sheets: list[str], aliases: list[str], fields: dict[str, list[str]]):
    names = {norm_header(s): s for s in sheets}
    sheet = next((names[norm_header(a)] for a in aliases if norm_header(a) in names), None)
    if sheet is None:
        return None, None, []
    table = read_table(path, sheet, _known_headers())
    hm = HeaderMap(table.headers, fields)
    return table, hm, list(table.rows())


def lithology_profile(layers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate interval continuity; returns (layers, flags)."""
    flags: list[str] = []
    prev_to = None
    prev_unit = None
    for layer in layers:
        row = layer["row"]
        start, end = parse_length(layer["from"]), parse_length(layer["to"])
        if start.value is None or end.value is None:
            flags.append(f"MISSING_INTERVAL_ENDPOINT_ROW_{row}")
            prev_to = None
            continue
        unit = start.unit or end.unit
        if end.value - start.value <= 0:
            flags.append(f"NON_POSITIVE_THICKNESS_ROW_{row}")
        if prev_to is not None and unit == prev_unit:
            if start.value > prev_to + 1e-6:
                flags.append(f"INTERVAL_GAP_BEFORE_ROW_{row}")
            elif start.value < prev_to - 1e-6:
                flags.append(f"INTERVAL_OVERLAP_AT_ROW_{row}")
        prev_to, prev_unit = end.value, unit
    return layers, flags


def comparable_layers(value: Any) -> list[tuple[str, ...]]:
    """Layer content without source-row provenance (for change detection)."""
    try:
        data = json.loads(value) if isinstance(value, str) else (value or [])
    except ValueError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            out.append(tuple(" ".join(str(item.get(k, "") or "").split()).casefold()
                             for k in (LITHO_KEYS if "lithology" in item else SURFACE_KEYS)))
    return out


def read(path: str, ctx: AdapterContext, fmt: dict[str, Any]) -> Iterator[SourceRecord]:
    sheets = list_sheets(path)
    tabs: dict[str, Any] = {}
    for tab, aliases in fmt["sheets"].items():
        tabs[tab] = _load_tab(path, sheets, aliases, fmt["fields"][tab])
    gen_table, gen_hm, gen_rows = tabs["general"]
    if gen_table is None:
        raise ValueError("Bore-log workbook has no 'General Information' tab")

    def by_id(tab: str, many: bool) -> dict[str, Any]:
        table, hm, rows = tabs[tab]
        out: dict[str, Any] = defaultdict(list) if many else {}
        if table is None:
            return out
        for num, values in rows:
            bid = norm_id(hm.get(values, "id"))
            if bid is None:
                continue
            if many:
                out[bid].append((num, values))
            elif bid not in out:
                out[bid] = (num, values)
        return out

    fixtures, wq, litho, surface = by_id("fixtures", False), by_id("water_quality", False), \
        by_id("lithology", True), by_id("surface", True)
    seen: dict[str, int] = {}
    for num, values in gen_rows:
        def g(name: str, hm: HeaderMap = gen_hm, row: list[str | None] = values) -> str | None:
            return hm.get(row, name)

        bid_raw = g("id")
        bid = norm_id(bid_raw)
        rec = SourceRecord("BORELOG", "borelog_workbook", ctx.file_name, gen_table.display_sheet, num, bid,
                           f"BORELOG|{bid or f'NO-ID-row{num}'}")
        if bid is None:
            rec.issue("MISSING_SOURCE_ID", "ERROR", "Bore-log ID is blank", "id")
        elif bid in seen:
            rec.issue("DUPLICATE_SOURCE_ID", "ERROR",
                      f"Bore-log ID {bid} already appears on General Information row {seen[bid]}", "id")
        else:
            seen[bid] = num
        tw_raw = clean(g("tw_id"))
        tw_code = norm_code(tw_raw)
        rec.identity_hints["tw_code"] = tw_code
        if tw_code is None:
            rec.issue("MISSING_TW_ID", "INFO", "No TW ID — cross-source verification with GPANI is not possible", "tw_id")
        rec.coords = resolve_coordinates(g("latitude"), g("longitude"))
        flags: list[str] = []
        c = rec.coords
        if c.status == "REVERSED_CORRECTED":
            flags.append("BORELOG_SWAPPED_SOURCE_COORDINATES")
            rec.issue("GPS_REVERSED", "INFO", "Source Latitude/Longitude columns were swapped; corrected", "latitude")
        elif not c.usable:
            flags.append("BORELOG_INVALID_OR_MISSING_GPS")
            rec.issue("GPS_INVALID" if c.status != "MISSING" else "GPS_MISSING", "WARNING",
                      f"Unusable coordinates ({c.lat_raw}, {c.lon_raw})", "latitude")
        rec.admin = ctx.admin.resolve({"division": g("division"), "district": g("district"), "upazila": g("upazila"),
                                       "union_name": g("union"), "village_name": g("village")})
        if not rec.admin.resolved.get("village_name"):
            flags.append("BORELOG_VILLAGE_MISSING")
        if not rec.admin.resolved.get("union_name"):
            flags.append("BORELOG_UNION_MISSING")
        if not all(rec.admin.resolved.get(lvl) for lvl in ("division", "district", "upazila")):
            flags.append("BORELOG_INCOMPLETE_ADMIN")
            rec.issue("MISSING_ADMIN", "WARNING", "Division/district/upazila incomplete", "division")
        type_key, type_display = normalize_well_type(g("tw_type"))
        rec.well_type = type_key
        if type_key is None:
            flags.append("BORELOG_TUBEWELL_TYPE_MISSING")
            rec.issue("MISSING_TUBEWELL_TYPE", "WARNING", "TW Type is blank", "tw_type")
        F = rec.fields
        F["borelog_id"] = FieldValue(bid, Policy.IDENTITY, raw=bid_raw, value_type="string")
        if tw_raw:
            F["borelog_tw_id_no"] = FieldValue(tw_raw, Policy.COMPARE, Category.METADATA, raw=tw_raw, value_type="string")
        for name, col, cat in (("project", "borelog_project_name", Category.METADATA),
                               ("drilling_method", "borelog_drilling_method", Category.METADATA),
                               ("drilling_started", "borelog_drilling_start_date", Category.METADATA),
                               ("drilling_ended", "borelog_drilling_end_date", Category.METADATA),
                               ("installation_started", "borelog_installation_start_date", Category.METADATA),
                               ("installation_ended", "borelog_installation_end_date", Category.METADATA),
                               ("screen_location", "borelog_screen_location_raw", Category.MEASUREMENT),
                               ("borehole_diameter", "borelog_borehole_diameter_raw", Category.MEASUREMENT),
                               ("well_diameter", "borelog_well_diameter_raw", Category.MEASUREMENT),
                               ("samples_collected", "borelog_lithological_samples_collected_raw", Category.METADATA),
                               ("logging_result", "borelog_logging_result_raw", Category.METADATA)):
            v = clean(g(name))
            if v is not None:
                F[col] = FieldValue(v, Policy.COMPARE, cat, raw=v, value_type="string")
        if c.lat_raw:
            F["borelog_latitude_raw"] = FieldValue(c.lat_raw, Policy.COMPARE, Category.METADATA, raw=c.lat_raw, value_type="string")
        if c.lon_raw:
            F["borelog_longitude_raw"] = FieldValue(c.lon_raw, Policy.COMPARE, Category.METADATA, raw=c.lon_raw, value_type="string")
        if type_display:
            F["borelog_tubewell_type"] = FieldValue(type_display, Policy.COMPARE, Category.METADATA, value_type="string")
        for name, raw_col, m_col, label in (("drilling_depth", "borelog_drilling_depth_raw", "borelog_drilling_depth_m", "DRILLING"),
                                            ("well_depth", "borelog_well_depth_raw", "borelog_well_depth_m", "WELL")):
            raw = clean(g(name))
            if raw is None:
                continue
            length = parse_length(raw)
            F[raw_col] = FieldValue(raw, Policy.COMPARE, Category.MEASUREMENT, raw=raw, value_type="string")
            if length.meters is not None:
                F[m_col] = FieldValue(length.meters, Policy.DERIVED, derived_from=raw_col, value_type="number")
                if label == "WELL":
                    rec.depth_m = length.meters
                if length.meters >= TEST_DEPTH_M:
                    flags.append("BORELOG_POSSIBLE_TEST_RECORD_REVIEW")
                    rec.issue("SUSPICIOUS_TEST_RECORD", "REVIEW",
                              f"{label.lower()} depth {raw!r} ({length.meters:,.0f} m) looks like a test or placeholder value", name)
                elif length.meters > UNREASONABLE_DEPTH_M:
                    rec.issue("UNREASONABLE_DEPTH", "WARNING", f"{label.lower()} depth {length.meters:,.0f} m is unusually large", name)
            elif length.value is not None:
                flags.append(f"BORELOG_{label}_DEPTH_UNIT_UNVERIFIED")
            else:
                rec.issue("DEPTH_UNPARSEABLE", "WARNING", f"{label.lower()} depth {raw!r} is not numeric", name)
            if "DECIMAL_COMMA_CORRECTED" in length.issues:
                flags.append(f"BORELOG_{label}_DEPTH_DECIMAL_COMMA_CORRECTED")
        drill = F.get("borelog_drilling_depth_m")
        well = F.get("borelog_well_depth_m")
        if drill and well and well.value > drill.value * 1.05 + 1:
            rec.issue("CONFLICTING_DEPTH", "WARNING", f"Well depth {well.value} m exceeds drilling depth {drill.value} m")
            flags.append("BORELOG_WELL_DEEPER_THAN_DRILLING")
        # ---- Well fixtures -------------------------------------------------
        ftab = tabs["fixtures"][1]
        if bid in fixtures:
            _fnum, fvals = fixtures[bid]
            for name, col in (("top_casing", "borelog_top_casing_raw"), ("blind_pipe", "borelog_blind_pipe_raw"),
                              ("screen_filter", "borelog_screen_filter_length_raw"), ("sand_trap", "borelog_sand_trap_raw"),
                              ("depth_of_logging", "borelog_depth_of_logging_raw")):
                v = clean(ftab.get(fvals, name))
                if v is not None:
                    F[col] = FieldValue(v, Policy.COMPARE, Category.MEASUREMENT, raw=v, value_type="string")
        # ---- Water quality -------------------------------------------------
        wtab = tabs["water_quality"][1]
        if bid in wq:
            _wnum, wvals = wq[bid]
            for name, col in (("lab_name", "borelog_wq_lab_name"), ("sampling_date", "borelog_wq_sampling_date"),
                              ("analysis_date", "borelog_wq_analysis_date")):
                v = clean(wtab.get(wvals, name))
                if v is not None:
                    F[col] = FieldValue(v, Policy.COMPARE, Category.METADATA, raw=v, value_type="string")
            for analyte in ("arsenic", "iron", "chloride", "manganese"):
                raw = clean(wtab.get(wvals, analyte))
                if raw is None:
                    continue
                m = parse_measurement(raw, analyte)
                raw_col, mg_col = f"borelog_wq_{analyte}_raw", f"borelog_wq_{analyte}_mg_l"
                F[raw_col] = FieldValue(raw, Policy.COMPARE, Category.MEASUREMENT, raw=raw, value_type="string")
                if m.mg_l is not None:
                    F[mg_col] = FieldValue(m.mg_l, Policy.DERIVED, derived_from=raw_col, value_type="number")
                flags.extend(f"BORELOG_{f}" for f in m.flags)
                if m.value is not None and m.value < 0:
                    rec.issue("NEGATIVE_MEASUREMENT", "WARNING", f"{analyte} reading {raw!r} is negative", raw_col)
        # ---- Lithology -----------------------------------------------------
        ltab = tabs["lithology"][1]
        layers = []
        for lnum, lvals in litho.get(bid, []) if bid else []:
            layer = {"row": lnum}
            for k in LITHO_KEYS:
                layer[k] = _raw_text(ltab.get(lvals, k))
            layers.append(layer)
        if layers:
            layers, litho_flags = lithology_profile(layers)
            profile = json.dumps(layers, ensure_ascii=False, separators=(",", ":"))
            if len(profile) > EXCEL_MAX_CELL_CHARS:
                rec.issue("LITHOLOGY_PROFILE_TOO_LONG", "REVIEW",
                          f"Lithology profile is {len(profile):,} characters (Excel limit {EXCEL_MAX_CELL_CHARS:,}); "
                          "it was not truncated and needs a decision", "lithology")
            else:
                F["borelog_lithology_profile_raw"] = FieldValue(profile, Policy.COMPARE, Category.MEASUREMENT,
                                                                group="lithology", value_type="string")
                F["borelog_lithology_layer_count"] = FieldValue(len(layers), Policy.DERIVED,
                                                                derived_from="borelog_lithology_profile_raw", value_type="number")
                if litho_flags:
                    F["borelog_lithology_quality_flags"] = FieldValue(";".join(litho_flags), Policy.DERIVED,
                                                                      derived_from="borelog_lithology_profile_raw",
                                                                      value_type="string")
                    flags.append("BORELOG_LITHOLOGY_INTERVAL_REVIEW")
        # ---- Surface water -------------------------------------------------
        stab = tabs["surface"][1]
        observations = []
        for snum, svals in surface.get(bid, []) if bid else []:
            obs = {"row": snum}
            for k in SURFACE_KEYS:
                obs[k] = _raw_text(stab.get(svals, k))
            observations.append(obs)
        if observations:
            F["borelog_surface_observations_raw"] = FieldValue(
                json.dumps(observations, ensure_ascii=False, separators=(",", ":")), Policy.COMPARE, Category.MEASUREMENT,
                group="surface", value_type="string")
            if len(observations) > 1:
                flags.append("BORELOG_MULTIPLE_SURFACE_OBSERVATIONS")
            for k, col in (("river", "borelog_surface_river_names_raw"), ("dry_flow", "borelog_surface_dry_flow_raw"),
                           ("dry_depth_ft", "borelog_surface_dry_depth_ft_raw"),
                           ("quality_opinion", "borelog_surface_quality_opinion_raw"),
                           ("dry_quality", "borelog_surface_dry_quality_raw")):
                vals = list(dict.fromkeys(o[k] for o in observations if clean(o[k])))
                if vals:
                    F[col] = FieldValue(" | ".join(vals), Policy.DERIVED, derived_from="borelog_surface_observations_raw",
                                        value_type="string")
        F["borelog_source_file"] = FieldValue(ctx.file_name, Policy.PROVENANCE, value_type="string")
        F["borelog_source_excel_row"] = FieldValue(num, Policy.PROVENANCE, value_type="number")
        rec.add_flag(*flags)
        rec.preview = {"ID": bid_raw, "TW ID No.": tw_raw, "Project": g("project"), "Division": g("division"),
                       "District": g("district"), "Upazila": g("upazila"), "Union": g("union"), "Village": g("village"),
                       "Latitude": g("latitude"), "Longitude": g("longitude"), "TW Type": g("tw_type"),
                       "Well Depth": g("well_depth"), "Lithology layers": len(layers), "Surface observations": len(observations)}
        if bid:
            rec.core = {
                "candidate_id": f"BORELOG:{bid}",
                **{lvl: rec.admin.resolved.get(lvl) for lvl in ("division", "district", "upazila", "union_name", "village_name")},
                "facility_type": type_display,
                "latitude": c.lat if c.usable else None,
                "longitude": c.lon if c.usable else None,
                "record_sources": "BORELOG",
                "match_status": "BORELOG_ONLY",
            }
        yield rec
