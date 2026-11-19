"""WPMS adapter (Water Point Management System tubewell lists).

The WPMS GO code is the source identity. Administrative names arrive partly in
Bangla; they are translated only through mappings learned from verified warehouse
rows. Water-quality free text is parsed into the ``wpms_*`` parameter columns while
the original text is preserved in ``wpms_water_quality_raw``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..chemistry import parse_wpms_water_quality
from ..geo import resolve_coordinates
from ..numbers import parse_length
from ..records import Category, FieldValue, Policy, SourceRecord
from ..text import clean, has_bangla, norm_id
from .base import AdapterContext, json_admin, normalize_well_type
from .tabular import HeaderMap, Table

DERIVED_WQ = (
    "wpms_arsenic_mg_l", "wpms_iron_mg_l", "wpms_chloride_mg_l", "wpms_manganese_mg_l", "wpms_ph_reported_value",
    "wpms_tds_mg_l", "wpms_faecal_coliform_reported_cfu", "wpms_total_coliform_reported_cfu",
    "wpms_electrical_conductivity_raw", "wpms_residual_chlorine_mg_l", "wpms_turbidity_ntu", "wpms_calcium_mg_l",
    "wpms_iodine_mg_l", "wpms_tetrachloroethylene_mg_l",
)


def read(table: Table, ctx: AdapterContext, fmt: dict[str, Any], fmt_name: str) -> Iterator[SourceRecord]:
    hm = HeaderMap(table.headers, fmt["fields"])
    for num, values in table.rows():
        def g(name: str) -> str | None:
            return hm.get(values, name)

        go_raw = g("go_code")
        go = norm_id(go_raw)
        if go is not None:
            go = go.replace(" ", "")
        rec = SourceRecord("WPMS", fmt_name, ctx.file_name, table.display_sheet, num, go, f"WPMS|{go or f'NO-ID-row{num}'}")
        rec.preview = {"GO Code": go_raw, "Division": g("division"), "District": g("district"), "Upazila": g("upazila"),
                       "Union": g("union"), "Village": g("village"), "Type": g("facility_type"),
                       "Total Depth (Meter)": g("total_depth"), "Water Quality": g("water_quality"),
                       "GPS Latitude": g("latitude"), "GPS Longitude": g("longitude"), "Current Status": g("status")}
        if go is None:
            rec.issue("MISSING_SOURCE_ID", "ERROR", "WPMS GO code is blank", "go_code")
        raw_admin = {"Division": clean(g("division")), "District": clean(g("district")), "Upazila": clean(g("upazila")),
                     "Union": clean(g("union")), "Village": clean(g("village"))}
        rec.admin = ctx.admin.resolve({"division": g("division"), "district": g("district"), "upazila": g("upazila"),
                                       "union_name": g("union"), "village_name": g("village")})
        flags: list[str] = []
        if any(has_bangla(v) for v in raw_admin.values() if v):
            flags.append("WPMS_ADMIN_NAMES_BANGLA")
        if rec.admin.unmapped:
            rec.issue("ADMIN_NAME_UNMAPPED", "WARNING",
                      f"Bangla names at {rec.admin.unmapped} have no verified English mapping in the warehouse")
        rec.coords = resolve_coordinates(g("latitude"), g("longitude"))
        c = rec.coords
        if c.status == "MISSING":
            flags.append("WPMS_MISSING_GPS")
            rec.issue("GPS_MISSING", "WARNING", "No GPS coordinates", "latitude")
        elif c.status == "REVERSED_CORRECTED":
            flags.append("WPMS_GPS_REVERSED_CORRECTED")
            rec.issue("GPS_REVERSED", "INFO", "Latitude/longitude were reversed; swapped", "latitude")
        elif not c.usable:
            flags.append("WPMS_GPS_OUTSIDE_BD_RANGE")
            rec.issue("GPS_INVALID", "WARNING", f"Coordinates ({c.lat_raw}, {c.lon_raw}) are outside Bangladesh", "latitude")
        ftype = clean(g("facility_type"))
        rec.well_type = normalize_well_type(ftype)[0]
        if ftype is None:
            rec.issue("MISSING_TUBEWELL_TYPE", "WARNING", "Facility type is blank", "facility_type")
        depth_raw = clean(g("total_depth"))
        depth = parse_length(depth_raw, default_unit="m")  # column header states metres
        rec.depth_m = depth.meters
        if depth_raw and depth.meters is None:
            rec.issue("DEPTH_UNPARSEABLE", "WARNING", f"Total depth {depth_raw!r} is not numeric", "total_depth")
        if "DECIMAL_COMMA_CORRECTED" in depth.issues:
            rec.issue("DEPTH_DECIMAL_COMMA", "INFO", f"Depth {depth_raw!r} used a decimal comma", "total_depth")
            flags.append("WPMS_DEPTH_DECIMAL_COMMA_CORRECTED")
        wq = parse_wpms_water_quality(g("water_quality"))
        flags.extend(wq.flags)
        if wq.raw:
            flags.append("WPMS_WATER_QUALITY_TEST_DATE_UNKNOWN")
        install = clean(g("installation_date"))
        if install is None:
            flags.append("WPMS_INSTALLATION_DATE_MISSING")
        F = rec.fields
        F["wpms_go_code"] = FieldValue(go, Policy.IDENTITY, raw=go_raw, value_type="string")
        if depth_raw:
            F["wpms_total_depth_raw"] = FieldValue(depth_raw, Policy.COMPARE, Category.METADATA, raw=depth_raw, value_type="string")
            if depth.meters is not None:
                F["wpms_total_depth_m"] = FieldValue(depth.meters, Policy.DERIVED, derived_from="wpms_total_depth_raw",
                                                     value_type="number")
        if wq.raw:
            F["wpms_water_quality_raw"] = FieldValue(wq.raw, Policy.COMPARE, Category.MEASUREMENT, raw=wq.raw,
                                                     value_type="string")
            for col, value in wq.values.items():
                F[col] = FieldValue(value, Policy.DERIVED, derived_from="wpms_water_quality_raw",
                                    value_type="string" if col.endswith("_raw") else "number")
        for name, col in (("installation_date", "wpms_installation_date"), ("platform_date", "wpms_platform_construction_date"),
                          ("status", "wpms_project_status_raw"), ("facility_type", "wpms_facility_type_raw"),
                          ("created_at", "wpms_created_at")):
            v = clean(g(name))
            if v is not None:
                F[col] = FieldValue(v, Policy.COMPARE, Category.METADATA, raw=v, value_type="string")
        if c.lat_raw:
            F["wpms_latitude_raw"] = FieldValue(c.lat_raw, Policy.COMPARE, Category.METADATA, raw=c.lat_raw, value_type="string")
        if c.lon_raw:
            F["wpms_longitude_raw"] = FieldValue(c.lon_raw, Policy.COMPARE, Category.METADATA, raw=c.lon_raw, value_type="string")
        if any(raw_admin.values()):
            F["wpms_admin_names_raw"] = FieldValue(json_admin(raw_admin, compact=True), Policy.FILL_ONLY, value_type="string")
        F["wpms_source_excel_row"] = FieldValue(num, Policy.PROVENANCE, value_type="number")
        rec.add_flag(*flags)
        if go:
            rec.core = {
                "candidate_id": f"WPMS:{go}",
                **{lvl: rec.admin.resolved.get(lvl) for lvl in ("division", "district", "upazila", "union_name", "village_name")},
                "facility_type": ftype,
                "latitude": c.lat if c.usable else None,
                "longitude": c.lon if c.usable else None,
                "record_sources": "WPMS",
                "match_status": "WPMS_ONLY",
                "match_evidence": "NO_IDENTICAL_GPANI_SOURCE_ID",
            }
        yield rec
