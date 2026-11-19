"""GPANI (Grameen Pani) adapter.

A GPANI geocode is a *location* code and is not, on its own, a unique physical-well
identifier (the verified warehouse contains geocodes shared by different wells).
The facility identity therefore follows the warehouse convention
``GP:{geocode}@{lat:.6f},{lon:.6f}`` built from the source coordinates. Source rows
with the same identity are collapsed into one record (``gpani_source_row_count``)
using a two-pass streaming group-by, so very large exports fit in memory.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

from ..geo import parse_coordinate, resolve_coordinates
from ..numbers import parse_length
from ..records import Category, FieldValue, Policy, SourceRecord
from ..text import clean, norm_code
from .base import AdapterContext, normalize_well_type
from .tabular import HeaderMap, Table

CHEMISTRY = (("arsenic", "gpani_arsenic_raw"), ("iron", "gpani_iron_raw"),
             ("chloride", "gpani_chloride_raw"), ("manganese", "gpani_manganese_raw"))


def _coord_text(value: str | None) -> str:
    num, _dec = parse_coordinate(value)
    return f"{num:.6f}" if num is not None else "NA"


def identity_key(geocode: str | None, lat: str | None, lon: str | None) -> str:
    return f"GP:{geocode}@{_coord_text(lat)},{_coord_text(lon)}"


def _row_signature(hm: HeaderMap, values: list[str | None]) -> str:
    parts = [clean(hm.get(values, f)) or "" for f in ("depth", "arsenic", "iron", "chloride", "manganese", "facility_type")]
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()


def read(table: Table, ctx: AdapterContext, fmt: dict[str, Any], fmt_name: str) -> Iterator[SourceRecord]:
    hm = HeaderMap(table.headers, fmt["fields"])
    community = fmt_name == "gpani_community"
    # Pass 1: group sizes and value consistency per facility identity.
    groups: dict[str, list[Any]] = {}
    for num, values in table.rows():
        geocode = norm_code(hm.get(values, "geocode"))
        if geocode is None:
            continue
        key = identity_key(geocode, hm.get(values, "latitude"), hm.get(values, "longitude"))
        sig = _row_signature(hm, values)
        entry = groups.get(key)
        if entry is None:
            groups[key] = [1, num, sig, False]
        else:
            entry[0] += 1
            if entry[2] != sig:
                entry[3] = True
    # Pass 2: one record per identity (first source row is representative).
    for num, values in table.rows():
        def g(name: str) -> str | None:
            return hm.get(values, name)

        geocode_raw = g("geocode")
        geocode = norm_code(geocode_raw)
        key = identity_key(geocode, g("latitude"), g("longitude")) if geocode else f"GP:NO-CODE@row{num}"
        entry = groups.get(key)
        if entry is not None and entry[1] != num:
            continue  # collapsed into the group's first row
        count = entry[0] if entry else 1
        rec = SourceRecord("GPANI", fmt_name, ctx.file_name, table.display_sheet, num, key if geocode else None,
                           f"GPANI|{key}")
        rec.identity_hints["geocode"] = geocode
        rec.preview = {"geo code": geocode_raw, "Division": g("division"), "District": g("district"),
                       "Upazila": g("upazila"), "Union": g("union"), "Village": g("village"),
                       "Latitude": g("latitude"), "Longitude": g("longitude"), "Depth": g("depth"),
                       "Type": g("facility_type"), "Arsenic": g("arsenic"), "Iron": g("iron")}
        if geocode is None:
            rec.issue("MISSING_SOURCE_ID", "ERROR", "GPANI geo code is blank", "geocode")
        if entry and entry[3]:
            rec.issue("DUPLICATE_SOURCE_ID", "REVIEW",
                      f"{count} source rows share this geocode and GPS but report different values", "geocode")
            rec.add_flag("GPANI_GROUP_VALUE_CONFLICT")
        elif count > 1:
            rec.add_flag("GPANI_EXACT_DUPLICATE_SOURCE_ROWS_COLLAPSED")
        rec.coords = resolve_coordinates(g("latitude"), g("longitude"))
        c = rec.coords
        if c.status == "MISSING":
            rec.add_flag("MISSING_GPS")
            rec.issue("GPS_MISSING", "WARNING", "No GPS coordinates", "latitude")
        elif c.status == "REVERSED_CORRECTED":
            rec.add_flag("GPS_REVERSED_CORRECTED")
            rec.issue("GPS_REVERSED", "INFO", "Latitude/longitude were reversed; swapped for the core columns", "latitude")
        elif not c.usable:
            rec.add_flag("INVALID_OR_REVERSED_GPS", "GPS_OUTSIDE_APPROX_BD_BOUNDS")
            rec.issue("GPS_INVALID", "WARNING", f"Coordinates ({c.lat_raw}, {c.lon_raw}) are outside Bangladesh", "latitude")
        rec.admin = ctx.admin.resolve({"division": g("division"), "district": g("district"), "upazila": g("upazila"),
                                       "union_name": g("union"), "village_name": g("village")})
        ftype_raw = clean(g("facility_type"))
        rec.well_type = normalize_well_type(ftype_raw)[0] if ftype_raw else ("COMMUNITY" if community else None)
        depth_raw = clean(g("depth"))
        depth = parse_length(depth_raw)
        rec.depth_m = depth.value  # unit not stated by GPANI; compared only as a number
        if depth_raw and depth.value is None:
            rec.issue("DEPTH_UNPARSEABLE", "WARNING", f"Depth {depth_raw!r} is not numeric", "depth")
        if "DECIMAL_COMMA_CORRECTED" in depth.issues:
            rec.issue("DEPTH_DECIMAL_COMMA", "INFO", f"Depth {depth_raw!r} used a decimal comma", "depth")
        F = rec.fields
        F["gpani_source_geocode"] = FieldValue(geocode, Policy.IDENTITY, raw=geocode_raw, value_type="string")
        if depth_raw:
            F["gpani_tubewell_depth_raw"] = FieldValue(depth_raw, Policy.COMPARE, Category.METADATA, raw=depth_raw,
                                                       value_type="string")
        for name, col in CHEMISTRY:
            raw = clean(g(name))
            if raw is not None:
                F[col] = FieldValue(raw, Policy.COMPARE, Category.MEASUREMENT, raw=raw, value_type="string")
        if not any(clean(g(n)) for n, _ in CHEMISTRY) and not community:
            rec.issue("NO_WATER_QUALITY", "INFO", "No water-quality readings in this GPANI row")
        # A partial export cannot contradict a count aggregated from the full registry.
        F["gpani_source_row_count"] = FieldValue(count, Policy.FILL_ONLY, Category.METADATA, value_type="number")
        if geocode:
            rec.add_flag("WATER_QUALITY_TEST_DATE_UNKNOWN")
            facility_type = ftype_raw or ("Community-based water facility (source category)" if community else None)
            rec.core = {
                "candidate_id": key,
                **{lvl: rec.admin.resolved.get(lvl) for lvl in ("division", "district", "upazila", "union_name", "village_name")},
                "facility_type": facility_type,
                "latitude": c.lat if c.usable else None,
                "longitude": c.lon if c.usable else None,
                "record_sources": "GPANI",
                "match_status": "GPANI_ONLY",
            }
        yield rec
