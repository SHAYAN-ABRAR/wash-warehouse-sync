"""AquaWatch adapters.

* ``aquawatch_survey``: annual / seasonal survey exports. One row = one observation
  of one groundwater resource (well) in one period. Observations are written into
  period columns ``aqua_{year}[_{season}]_{metric}`` of the well's facility row.
* ``aquawatch_historical``: the 1986–2017 GWC table. Values are preserved exactly;
  their measurement definition is unverified and they are never reinterpreted.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Any

from ..chemistry import parse_measurement
from ..geo import resolve_coordinates
from ..numbers import parse_number, strict_number
from ..records import Category, FieldValue, Policy, SourceRecord
from ..text import clean, norm_id, to_text
from .base import AdapterContext, json_admin, normalize_well_type, parse_survey_period
from .tabular import HeaderMap, Table

WATER_TABLE_PLAUSIBLE_M = (0.0, 100.0)


def _gps_flags(rec: SourceRecord, prefix: str) -> None:
    c = rec.coords
    if c is None:
        return
    if c.status == "MISSING":
        rec.add_flag(f"{prefix}_MISSING_GPS")
        rec.issue("GPS_MISSING", "WARNING", "No GPS coordinates; geographic duplicate checks are not possible", "latitude")
    elif c.status == "REVERSED_CORRECTED":
        rec.add_flag(f"{prefix}_GPS_REVERSED_CORRECTED")
        rec.issue("GPS_REVERSED", "INFO", "Latitude/longitude were reversed in the source and have been swapped", "latitude")
    elif c.status in ("OUT_OF_RANGE", "INVALID"):
        rec.add_flag(f"{prefix}_MISSING_OR_INVALID_GPS")
        rec.issue("GPS_INVALID", "WARNING", f"Coordinates ({c.lat_raw}, {c.lon_raw}) are outside Bangladesh or invalid", "latitude")
    if c.usable and c.precision and c.precision <= 3:
        rec.add_flag(f"{prefix}_GPS_APPROXIMATE_{c.precision}_DECIMAL")


def read_survey(table: Table, ctx: AdapterContext, fmt: dict[str, Any], fmt_name: str) -> Iterator[SourceRecord]:
    hm = HeaderMap(table.headers, fmt["fields"])
    for num, values in table.rows():
        def g(name: str) -> str | None:
            return hm.get(values, name)

        rid_raw = g("resource_id")
        rid = norm_id(rid_raw)
        period, problem = parse_survey_period(g("survey_year"))
        if ctx.forced_period is not None and period is not None and period != ctx.forced_period:
            problem = f"Row period {period.label} differs from the selected period {ctx.forced_period.label}"
        key = f"AQUAWATCH|{rid or 'NO-ID'}|{period.key if period else 'UNKNOWN'}"
        rec = SourceRecord("AQUAWATCH", fmt_name, ctx.file_name, table.display_sheet, num, rid, key, period=period)
        rec.preview = {"Survey Year": g("survey_year"), "Ground Water Resource": rid_raw, "Type": g("well_type"),
                       "Depth": g("depth"), "Water Table (m)": g("water_table"), "Arsenic": g("arsenic"),
                       "Iron": g("iron"), "Chloride": g("chloride"), "Division": g("division"),
                       "District": g("district"), "Upazila": g("upazila"), "Union": g("union"),
                       "Latitude": g("latitude"), "Longitude": g("longitude")}
        if rid is None:
            rec.issue("MISSING_SOURCE_ID", "ERROR", "Ground Water Resource ID is blank", "resource_id")
        elif not re.search(r"\d", rid):
            rec.issue("NON_NUMERIC_SOURCE_ID", "WARNING", f"Resource ID {rid!r} contains no digits", "resource_id")
        if problem:
            severity = "REVIEW" if period is not None else "ERROR"
            rec.issue("SURVEY_PERIOD_PROBLEM", severity, problem, "survey_year")
        if period is not None and period.year < 2018:
            rec.issue("HISTORICAL_PERIOD_SURVEY", "REVIEW",
                      f"Survey period {period.label} overlaps the historical 1986–2017 table whose definitions are unverified",
                      "survey_year")
        rec.coords = resolve_coordinates(g("latitude"), g("longitude"))
        _gps_flags(rec, "AQUA")
        rec.admin = ctx.admin.resolve({"division": g("division"), "district": g("district"), "upazila": g("upazila"),
                                       "union_name": g("union"), "village_name": None})
        if rec.admin.unmapped:
            rec.issue("ADMIN_NAME_UNMAPPED", "WARNING", f"Unmapped Bangla admin names at {rec.admin.unmapped}")
        type_key, type_display = normalize_well_type(g("well_type"))
        rec.well_type = type_key
        if type_key is None:
            rec.issue("MISSING_TUBEWELL_TYPE", "WARNING", "Type of tubewell is blank", "well_type")
        depth = parse_number(g("depth"))
        rec.depth_m = depth.value
        if depth.value is None and g("depth"):
            rec.issue("DEPTH_UNPARSEABLE", "WARNING", f"Depth {g('depth')!r} is not numeric", "depth")
        elif depth.value is None:
            rec.add_flag("AQUA_MISSING_WELL_DEPTH")
        F = rec.fields
        F["aqua_gwc_id"] = FieldValue(rid, Policy.IDENTITY, raw=rid_raw, value_type="string")
        if type_display:
            F["aqua_well_type"] = FieldValue(type_display, Policy.FILL_ONLY, raw=g("well_type"), value_type="string")
        if depth.value is not None:
            F["aqua_well_depth_m"] = FieldValue(depth.value, Policy.FILL_ONLY, raw=g("depth"), value_type="number")
        if rec.coords.lat_raw:
            F["aqua_latitude_raw"] = FieldValue(rec.coords.lat_raw, Policy.FILL_ONLY, raw=rec.coords.lat_raw, value_type="string")
        if rec.coords.lon_raw:
            F["aqua_longitude_raw"] = FieldValue(rec.coords.lon_raw, Policy.FILL_ONLY, raw=rec.coords.lon_raw, value_type="string")
        raw_admin = {"Division": clean(g("division")), "District": clean(g("district")),
                     "Upazila": clean(g("upazila")), "Union": clean(g("union"))}
        if any(raw_admin.values()):
            F["aqua_admin_names_raw"] = FieldValue(json_admin(raw_admin, compact=False), Policy.FILL_ONLY, value_type="string")
        if period is not None:
            group = period.key
            measured = 0
            for metric, field_name in (("arsenic_raw", "arsenic"), ("iron_raw", "iron"), ("chloride_raw", "chloride")):
                raw = clean(g(field_name))
                if raw is None:
                    continue
                measured += 1
                analyte = field_name
                m = parse_measurement(raw, analyte)
                for flag in m.flags:
                    if flag.endswith(("NEGATIVE_VALUE", "IMPLAUSIBLE_VALUE")):
                        rec.issue(f"{analyte.upper()}_IMPLAUSIBLE", "WARNING", f"{analyte} reading {raw!r} is implausible", field_name)
                F[period.column(metric)] = FieldValue(raw, Policy.COMPARE, Category.MEASUREMENT, raw=raw, group=group)
            wt_raw = clean(g("water_table"))
            if wt_raw is not None:
                measured += 1
                wt = parse_number(wt_raw)
                if strict_number(wt_raw) is None:
                    rec.issue("WATER_TABLE_NON_NUMERIC", "WARNING", f"Water table {wt_raw!r} is not numeric", "water_table")
                    F[period.column("water_table_m")] = FieldValue(wt_raw, Policy.COMPARE, Category.MEASUREMENT, raw=wt_raw,
                                                                   group=group, value_type="string")
                else:
                    if not (WATER_TABLE_PLAUSIBLE_M[0] <= wt.value <= WATER_TABLE_PLAUSIBLE_M[1]):
                        rec.issue("SUSPICIOUS_WATER_TABLE", "WARNING",
                                  f"Water table {wt_raw} m is outside the plausible range 0–100 m", "water_table")
                        rec.add_flag(f"{period.source_token}_WATER_TABLE_OUTLIER")
                    F[period.column("water_table_m")] = FieldValue(wt.value, Policy.COMPARE, Category.MEASUREMENT, raw=wt_raw,
                                                                   group=group, value_type="number")
            else:
                rec.issue("MISSING_WATER_TABLE", "WARNING", "AquaWatch survey row has no water-table measurement", "water_table")
            if measured == 0:
                rec.issue("NO_MEASUREMENTS", "ERROR", "Survey row has no measurements for the period", None)
            F[period.column("source_row")] = FieldValue(num, Policy.PROVENANCE, group=group, value_type="number")
            if any(F.get(period.column(m)) for m in ("arsenic_raw", "iron_raw", "chloride_raw")):
                rec.add_flag(f"{period.source_token}_WATER_QUALITY_UNITS_UNVERIFIED")
            if period.season is None and period.year >= 2025:
                rec.add_flag(f"AQUA_{period.year}_SEASON_UNSPECIFIED")
        if period is not None and rid is not None:
            cand = f"{period.candidate_prefix}:{rid}"
            rec.core = _new_core(rec, cand, type_display, period.source_token,
                                 match_evidence=(f"AquaWatch {period.label} resource ID; no existing warehouse row carries "
                                                 "this ID and no verified cross-source identity was found"),
                                 extra={"aqua_record_key": cand, "aqua_match_status": "NEW_RESOURCE",
                                        "aqua_source_row_numbers": str(num), "aqua_source_row_count": 1})
        yield rec


def _new_core(rec: SourceRecord, candidate_id: str, type_display: str | None, source_token: str,
              match_evidence: str, extra: dict[str, Any]) -> dict[str, Any]:
    admin = rec.admin.resolved if rec.admin else {}
    c = rec.coords
    core = {
        "candidate_id": candidate_id,
        "division": admin.get("division"),
        "district": admin.get("district"),
        "upazila": admin.get("upazila"),
        "union_name": admin.get("union_name"),
        "village_name": admin.get("village_name"),
        "facility_type": type_display or "Tubewell (type unreported)",
        "latitude": c.lat if c and c.usable else None,
        "longitude": c.lon if c and c.usable else None,
        "record_sources": source_token,
        "match_status": "NEW_AQUA_RESOURCE",
        "match_evidence": match_evidence,
    }
    core.update(extra)
    return core


def _coord_key(value: Any) -> str:
    number = strict_number(value)
    return f"{number:.6f}" if number is not None else "NA"


def read_historical(table: Table, ctx: AdapterContext, fmt: dict[str, Any], fmt_name: str) -> Iterator[SourceRecord]:
    hm = HeaderMap(table.headers, fmt["fields"])
    year_cols = [(i, h.strip()) for i, h in enumerate(table.headers) if re.match(fmt["year_columns_regex"], h.strip())]
    for num, values in table.rows():
        def g(name: str) -> str | None:
            return hm.get(values, name)

        gwc_raw = g("gwc_id")
        gwc = norm_id(gwc_raw)
        type_key, type_display = normalize_well_type(g("well_type"))
        lat_key = _coord_key(g("latitude"))
        lon_key = _coord_key(g("longitude"))
        # GWC ids are reused for paired (and occasionally relocated) stations, so the
        # historical identity is id + type + station coordinates.
        depth_key = _coord_key(g("depth_ft")).rstrip("0").rstrip(".") if strict_number(g("depth_ft")) is not None else "NA"
        key = f"AQUAWATCH_HIST|{gwc or 'NO-ID'}|{type_key or 'UNKNOWN'}|{lat_key}|{lon_key}|{depth_key}"
        rec = SourceRecord("AQUAWATCH", fmt_name, ctx.file_name, table.display_sheet, num, gwc, key)
        rec.identity_hints["historical"] = True
        rec.identity_hints["station"] = (lat_key, lon_key)
        rec.well_type = type_key
        rec.preview = {"GWC": gwc_raw, "Well type": g("well_type"), "Division": g("division"), "District": g("district"),
                       "Upazila": g("upazila"), "Union": g("union"), "Lat": g("latitude"), "Long": g("longitude")}
        if gwc is None:
            rec.issue("MISSING_SOURCE_ID", "ERROR", "GWC identifier is blank", "gwc_id")
        if type_key is None:
            rec.issue("MISSING_TUBEWELL_TYPE", "ERROR",
                      "Well type is required for historical GWC identity (GWC ids are reused for paired wells)", "well_type")
        rec.coords = resolve_coordinates(g("latitude"), g("longitude"))
        _gps_flags(rec, "AQUA")
        rec.admin = ctx.admin.resolve({"division": g("division"), "district": g("district"), "upazila": g("upazila"),
                                       "union_name": g("union"), "village_name": None})
        F = rec.fields
        F["aqua_gwc_id"] = FieldValue(gwc, Policy.IDENTITY, raw=gwc_raw, value_type="string")
        if type_display:
            F["aqua_well_type"] = FieldValue(type_display, Policy.FILL_ONLY, value_type="string")
        for name, col in (("depth_ft", "aqua_well_depth_ft"), ("depth_m", "aqua_well_depth_m")):
            n = parse_number(g(name))
            if n.value is not None:
                F[col] = FieldValue(n.value, Policy.FILL_ONLY, raw=g(name), value_type="number")
        rec.depth_m = parse_number(g("depth_m")).value
        for i, year in year_cols:
            raw = values[i] if i < len(values) else None
            text = to_text(raw)
            if text is None:
                continue
            number = strict_number(text)
            value: Any = number if number is not None else text  # corrupt/non-numeric text is preserved verbatim
            F[f"aqua_{year}"] = FieldValue(value, Policy.COMPARE, Category.MEASUREMENT, raw=text, group="historical",
                                           value_type="number" if isinstance(value, float) else "string")
        rec.add_flag("AQUA_HISTORICAL_MEASUREMENT_TYPE_AND_UNITS_UNVERIFIED")
        if gwc and type_key:
            digest = hashlib.sha256(f"{gwc}|{type_key}|{g('latitude')}|{g('longitude')}".encode()).hexdigest()[:18]
            cand = f"AQUA:{gwc}:{type_key.replace(' ', '_')}:{digest}"
            rec.core = _new_core(rec, cand, f"{type_display} well (historical GWC)", "AQUA_HISTORICAL_CSV",
                                 match_evidence="Historical GWC station; no verified common physical-facility identifier",
                                 extra={"aqua_record_key": cand, "aqua_match_status": "NO_VERIFIED_FACILITY_CROSSWALK",
                                        "aqua_source_row_numbers": str(num), "aqua_source_row_count": 1})
            rec.core["match_status"] = "AQUA_NOT_CROSS_SOURCE_MATCHED"
        yield rec
