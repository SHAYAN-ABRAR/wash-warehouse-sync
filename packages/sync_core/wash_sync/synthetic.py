"""100% synthetic data generation (scale tests, fixtures, public demo).

Nothing here is derived from real records: identifiers are prefixed ``SYN``,
sub-district names are generated from syllables, coordinates are random points in
coarse division boxes and all measurements are random. Only the *column structure*
mirrors the real warehouse so that the engine can be exercised end to end.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .schema import col_letter
from .xlsx.create import WAREHOUSE_STYLES_XML, SheetSpec, write_workbook

CORE = ["candidate_id", "division", "district", "upazila", "union_name", "village_name", "facility_type", "latitude",
        "longitude", "record_sources", "match_status", "match_evidence", "quality_flags"]
GPANI = ["gpani_source_geocode", "gpani_tubewell_depth_raw", "gpani_arsenic_raw", "gpani_iron_raw", "gpani_chloride_raw",
         "gpani_manganese_raw", "gpani_source_row_count"]
WPMS = ["wpms_go_code", "wpms_total_depth_m", "wpms_total_depth_raw", "wpms_arsenic_mg_l", "wpms_iron_mg_l",
        "wpms_chloride_mg_l", "wpms_manganese_mg_l", "wpms_ph_reported_value", "wpms_tds_mg_l",
        "wpms_faecal_coliform_reported_cfu", "wpms_total_coliform_reported_cfu", "wpms_electrical_conductivity_raw",
        "wpms_residual_chlorine_mg_l", "wpms_turbidity_ntu", "wpms_calcium_mg_l", "wpms_iodine_mg_l",
        "wpms_tetrachloroethylene_mg_l", "wpms_water_quality_raw", "wpms_installation_date",
        "wpms_platform_construction_date", "wpms_project_status_raw", "wpms_facility_type_raw", "wpms_latitude_raw",
        "wpms_longitude_raw", "wpms_admin_names_raw", "wpms_created_at", "wpms_source_excel_row", "wpms_quality_flags"]
AQUA_META = ["aqua_gwc_id", "aqua_well_type", "aqua_well_depth_m", "aqua_well_depth_ft", "aqua_record_key",
             "aqua_match_status", "aqua_source_row_numbers", "aqua_source_row_count", "aqua_latitude_raw",
             "aqua_longitude_raw", "aqua_admin_names_raw", "aqua_quality_flags"]
AQUA_HIST = [f"aqua_{y}" for y in range(1986, 2018)]
METRICS = ["arsenic_raw", "water_table_m", "iron_raw", "chloride_raw", "source_row"]
ANNUAL = [f"aqua_{y}_{m}" for y in range(2018, 2025) for m in METRICS]
SEASONAL = [f"aqua_{p}_{m}" for p in ("2025_wet", "2025", "2026_dry", "2026_wet") for m in METRICS]
BORELOG = ["borelog_id", "borelog_tw_id_no", "borelog_project_name", "borelog_source_file", "borelog_source_excel_row",
           "borelog_match_status", "borelog_quality_flags", "borelog_latitude_raw", "borelog_longitude_raw",
           "borelog_tubewell_type", "borelog_drilling_method", "borelog_drilling_start_date", "borelog_drilling_end_date",
           "borelog_installation_start_date", "borelog_installation_end_date", "borelog_drilling_depth_raw",
           "borelog_drilling_depth_m", "borelog_well_depth_raw", "borelog_well_depth_m", "borelog_screen_location_raw",
           "borelog_borehole_diameter_raw", "borelog_well_diameter_raw", "borelog_lithological_samples_collected_raw",
           "borelog_logging_result_raw", "borelog_top_casing_raw", "borelog_blind_pipe_raw",
           "borelog_screen_filter_length_raw", "borelog_sand_trap_raw", "borelog_depth_of_logging_raw",
           "borelog_wq_lab_name", "borelog_wq_sampling_date", "borelog_wq_analysis_date", "borelog_wq_arsenic_raw",
           "borelog_wq_arsenic_mg_l", "borelog_wq_iron_raw", "borelog_wq_iron_mg_l", "borelog_wq_chloride_raw",
           "borelog_wq_chloride_mg_l", "borelog_wq_manganese_raw", "borelog_wq_manganese_mg_l",
           "borelog_lithology_layer_count", "borelog_lithology_profile_raw", "borelog_lithology_quality_flags",
           "borelog_surface_river_names_raw", "borelog_surface_dry_flow_raw", "borelog_surface_dry_depth_ft_raw",
           "borelog_surface_quality_opinion_raw", "borelog_surface_dry_quality_raw", "borelog_surface_observations_raw"]
WAREHOUSE_HEADERS = CORE + GPANI + WPMS + AQUA_META + AQUA_HIST + ANNUAL + SEASONAL + BORELOG
assert len(WAREHOUSE_HEADERS) == 196, len(WAREHOUSE_HEADERS)

STRING_COLUMNS = set(CORE[:7] + CORE[9:]) | {"gpani_source_geocode", "gpani_tubewell_depth_raw", "gpani_arsenic_raw",
    "gpani_iron_raw", "gpani_chloride_raw", "gpani_manganese_raw"} | {c for c in WPMS if c.endswith(("_raw", "_code",
    "_date", "_at", "_flags"))} | {"aqua_gwc_id", "aqua_well_type", "aqua_record_key", "aqua_match_status",
    "aqua_source_row_numbers", "aqua_latitude_raw", "aqua_longitude_raw", "aqua_admin_names_raw", "aqua_quality_flags"} | {
    c for c in ANNUAL if c.endswith(("arsenic_raw", "iron_raw", "chloride_raw"))} | {
    c for c in SEASONAL if c.startswith("aqua_2025_wet")} | {c for c in BORELOG if not c.endswith(("_m", "_mg_l", "_count", "_row"))}

DIVISIONS: dict[str, tuple[float, float, float, float, str]] = {
    # name: (lat_min, lat_max, lon_min, lon_max, Bangla name)
    "BARISAL": (22.05, 22.95, 89.95, 90.85, "বরিশাল"),
    "CHITTAGONG": (21.6, 23.4, 91.1, 92.2, "চট্টগ্রাম"),
    "DHAKA": (23.3, 24.25, 89.85, 90.85, "ঢাকা"),
    "KHULNA": (22.05, 23.45, 88.85, 89.75, "খুলনা"),
    "MYMENSINGH": (24.35, 25.15, 89.95, 90.95, "ময়মনসিংহ"),
    "RAJSHAHI": (24.05, 25.15, 88.25, 89.45, "রাজশাহী"),
    "RANGPUR": (25.25, 26.35, 88.45, 89.75, "রংপুর"),
    "SYLHET": (24.25, 25.05, 91.05, 92.25, "সিলেট"),
}
SYL = ["nil", "sona", "kash", "bel", "tara", "rup", "hiz", "mol", "dhan", "kal", "sap", "gor", "pala", "ruk", "teel",
       "ban", "jhau", "kadam", "shimul", "bakul", "amra", "jam", "lebu", "noya", "puran", "char", "majh", "uttar"]
SUF = ["pur", "ganj", "nagar", "bari", "hat", "khali", "danga", "tola", "chak", "dia", "kandi", "para"]
TYPES = [("Deep Tube Well Other Pumps (Code 06)", "DEEP"), ("Deep Tube Well No. 6 pump (Code 04)", "DEEP"),
         ("Shallow Tube Well No. 6 pump (Code 01)", "SHALLOW"), ("Shallow Tube Well Other Pumps (Code 03)", "SHALLOW"),
         ("Shallow Tube Well Tara dev head pump/Tara Pump (Code 02)", "SHALLOW"), ("Rain Water Harvester (Code 11)", "RWH")]


def header_styles() -> list[str]:
    out = []
    for name in WAREHOUSE_HEADERS:
        if name in CORE:
            out.append("1")
        elif name.startswith("gpani_"):
            out.append("2")
        elif name.startswith("wpms_"):
            out.append("3")
        elif name in SEASONAL:
            out.append("5")
        elif name.startswith("aqua_"):
            out.append("4")
        else:
            out.append("6")
    return out


def data_styles() -> list[str | None]:
    return [("5" if n in ("borelog_id", "borelog_tw_id_no") else None) if n.startswith("borelog_") else "6"
            for n in WAREHOUSE_HEADERS]


def widths() -> dict[int, float]:
    return {i + 1: (54.71 if n == "candidate_id" else 30.71 if n in SEASONAL else 24.71) for i, n in enumerate(WAREHOUSE_HEADERS)}


def _name(rng: random.Random) -> str:
    return (rng.choice(SYL) + rng.choice(SUF)).upper()


@dataclass
class Geo:
    division: str
    district: str
    upazila: str
    union: str
    village: str
    lat: float
    lon: float


@dataclass
class SyntheticWorld:
    """Small, internally consistent synthetic warehouse + source universe."""

    seed: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    admin_tree: dict[str, dict[str, dict[str, dict[str, list[str]]]]] = field(default_factory=dict)

    def sorted_rows(self) -> list[dict[str, Any]]:
        return sorted(self.rows, key=lambda r: (r.get("division") or "", r.get("district") or "", r.get("upazila") or "",
                                                r.get("union_name") or "", r.get("village_name") or "", r["candidate_id"]))


def build_admin_tree(rng: random.Random, districts: int = 3, upazilas: int = 3, unions: int = 3, villages: int = 4):
    tree: dict[str, Any] = {}
    for div in DIVISIONS:
        tree[div] = {}
        for _ in range(districts):
            d = _name(rng)
            tree[div][d] = {}
            for _ in range(upazilas):
                u = _name(rng)
                tree[div][d][u] = {_name(rng): sorted({_name(rng) for _ in range(villages)}) for _ in range(unions)}
    return tree


def random_geo(rng: random.Random, tree: dict[str, Any], division: str | None = None) -> Geo:
    div = division or rng.choice(list(tree))
    d = rng.choice(list(tree[div]))
    u = rng.choice(list(tree[div][d]))
    un = rng.choice(list(tree[div][d][u]))
    v = rng.choice(tree[div][d][u][un])
    la0, la1, lo0, lo1, _bn = DIVISIONS[div]
    return Geo(div, d, u, un, v, round(rng.uniform(la0, la1), 6), round(rng.uniform(lo0, lo1), 6))


def _num_text(rng: random.Random, lo: float, hi: float, digits: int = 3) -> str:
    return f"{rng.uniform(lo, hi):.{digits}f}"


def make_gpani(rng: random.Random, geo: Geo, n: int) -> dict[str, Any]:
    code = f"99{n:021d}"
    ftype, _ = rng.choice(TYPES[:5])
    lat, lon = geo.lat, geo.lon
    return {
        "candidate_id": f"GP:{code}@{lat:.6f},{lon:.6f}", "division": geo.division, "district": geo.district,
        "upazila": geo.upazila, "union_name": geo.union, "village_name": geo.village, "facility_type": ftype,
        "latitude": lat, "longitude": lon, "record_sources": "GPANI", "match_status": "GPANI_ONLY",
        "quality_flags": "WATER_QUALITY_TEST_DATE_UNKNOWN", "gpani_source_geocode": code,
        "gpani_tubewell_depth_raw": str(rng.randint(60, 340)), "gpani_arsenic_raw": _num_text(rng, 0.001, 0.08),
        "gpani_iron_raw": _num_text(rng, 0.05, 4), "gpani_chloride_raw": str(rng.randint(5, 600)),
        "gpani_source_row_count": 1,
    }


def make_wpms(rng: random.Random, geo: Geo, n: int) -> dict[str, Any]:
    code = f"98{n:021d}"
    ftype = rng.choice(["Deep Tube Well Other Pumps", "Shallow Tube Well No. 6 pump", "Deep Tube Well No. 6 pump"])
    depth = round(rng.uniform(40, 330), 2)
    arsenic, iron = round(rng.uniform(0.001, 0.05), 4), round(rng.uniform(0.05, 3), 3)
    bn = DIVISIONS[geo.division][4]
    return {
        "candidate_id": f"WPMS:{code}", "division": geo.division, "district": geo.district, "upazila": geo.upazila,
        "union_name": geo.union, "village_name": geo.village, "facility_type": ftype, "latitude": geo.lat,
        "longitude": geo.lon, "record_sources": "WPMS", "match_status": "WPMS_ONLY",
        "match_evidence": "NO_IDENTICAL_GPANI_SOURCE_ID", "quality_flags": "WPMS_ADMIN_NAMES_BANGLA",
        "wpms_go_code": code, "wpms_total_depth_m": depth, "wpms_total_depth_raw": f"{depth:.2f}",
        "wpms_arsenic_mg_l": arsenic, "wpms_iron_mg_l": iron,
        "wpms_water_quality_raw": f"Arsenic: {arsenic} mg/L; Iron: {iron} mg/L",
        "wpms_installation_date": f"2024-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}", "wpms_project_status_raw": "চলমান",
        "wpms_facility_type_raw": ftype, "wpms_latitude_raw": f"{geo.lat:.6f}", "wpms_longitude_raw": f"{geo.lon:.6f}",
        "wpms_admin_names_raw": json.dumps({"Division": bn, "District": geo.district.title(), "Upazila": geo.upazila.title(),
                                            "Union": geo.union, "Village": geo.village}, ensure_ascii=False,
                                           separators=(",", ":")),
        "wpms_created_at": "2025-01-15 10:00:00+00:00", "wpms_source_excel_row": n + 2,
        "wpms_quality_flags": "WPMS_ADMIN_NAMES_BANGLA",
    }


def make_aqua(rng: random.Random, geo: Geo, n: int, periods: list[str]) -> dict[str, Any]:
    rid = f"SYN-{n:06d}"
    wtype = rng.choice(["Deep", "Shallow", "Moderately Deep"])
    first = periods[0]
    prefix = "AQUA" + first.replace("_", "").upper()
    row: dict[str, Any] = {
        "candidate_id": f"{prefix}:{rid}", "division": geo.division, "district": geo.district, "upazila": geo.upazila,
        "union_name": geo.union, "facility_type": wtype, "latitude": geo.lat, "longitude": geo.lon,
        "record_sources": ";".join("AQUA_" + p.upper() for p in periods), "match_status": "NEW_AQUA_RESOURCE",
        "aqua_gwc_id": rid, "aqua_well_type": wtype, "aqua_well_depth_m": round(rng.uniform(20, 320), 2),
        "aqua_record_key": f"{prefix}:{rid}", "aqua_match_status": "NEW_RESOURCE",
        "aqua_latitude_raw": f"{geo.lat:.6f}", "aqua_longitude_raw": f"{geo.lon:.6f}",
        "aqua_admin_names_raw": json.dumps({"Division": geo.division.title(), "District": geo.district.title(),
                                            "Upazila": geo.upazila.title(), "Union": geo.union.title()}, ensure_ascii=False),
    }
    for i, p in enumerate(periods):
        numeric = p not in ("2025_wet",) and not p.startswith(("2018", "2019", "2020", "2021", "2022", "2023", "2024"))
        conv: Callable[[str], Any] = (lambda s: float(s)) if numeric else (lambda s: s)
        row[f"aqua_{p}_arsenic_raw"] = conv(_num_text(rng, 0.001, 0.09))
        row[f"aqua_{p}_water_table_m"] = round(rng.uniform(2, 12), 2) if p != "2025_wet" else f"{rng.uniform(2, 12):.2f}"
        row[f"aqua_{p}_iron_raw"] = conv(_num_text(rng, 0.05, 3))
        row[f"aqua_{p}_chloride_raw"] = conv(str(rng.randint(10, 400)))
        row[f"aqua_{p}_source_row"] = n + 2 + i if p != "2025_wet" else str(n + 2 + i)
    return row


def make_borelog(rng: random.Random, geo: Geo, n: int) -> dict[str, Any]:
    bid = f"SYNBL{n:07d}"
    depth = rng.randint(30, 300)
    layers = []
    top = 0
    for i in range(rng.randint(3, 7)):
        bottom = top + rng.choice([3, 6, 9, 12])
        layers.append({"row": 2 + n * 10 + i, "from": f"{top} m", "to": f"{bottom} m", "color": rng.choice(["Grey", "Brown"]),
                       "color_code": "C 1", "lithology": rng.choice(["Clay (Cl)", "Fine Sand (FS)", "Medium Sand (MS)"]),
                       "lithology_code": "S 1", "remarks": ""})
        top = bottom
    return {
        "candidate_id": f"BORELOG:{bid}", "division": geo.division, "district": geo.district, "upazila": geo.upazila,
        "union_name": geo.union, "facility_type": "Deep" if depth > 150 else "Shallow", "latitude": geo.lat,
        "longitude": geo.lon, "record_sources": "BORELOG", "match_status": "BORELOG_ONLY",
        "match_evidence": "NEW_NO_CODE_MATCH", "borelog_id": bid, "borelog_tw_id_no": f"97{n:021d}",
        "borelog_project_name": "SYN-PROJ", "borelog_source_file": "synthetic_bore_logs.xlsx",
        "borelog_source_excel_row": n + 2, "borelog_match_status": "NEW_NO_CODE_MATCH",
        "borelog_latitude_raw": f"{geo.lat:.6f}", "borelog_longitude_raw": f"{geo.lon:.6f}",
        "borelog_tubewell_type": "Deep" if depth > 150 else "Shallow", "borelog_well_depth_raw": f"{depth} m",
        "borelog_well_depth_m": float(depth), "borelog_drilling_depth_raw": f"{depth + 3} m",
        "borelog_drilling_depth_m": float(depth + 3), "borelog_wq_arsenic_raw": "0.002 Mg/l", "borelog_wq_arsenic_mg_l": 0.002,
        "borelog_lithology_layer_count": len(layers),
        "borelog_lithology_profile_raw": json.dumps(layers, ensure_ascii=False, separators=(",", ":")),
        "borelog_surface_observations_raw": json.dumps([{"row": n + 2, "river": "", "dry_flow": "", "quality_opinion": "",
                                                         "dry_depth_ft": "", "dry_quality": ""}], separators=(",", ":")),
    }


def build_world(seed: int = 2026, gpani: int = 1800, wpms: int = 260, aqua: int = 220, borelog: int = 60) -> SyntheticWorld:
    rng = random.Random(seed)
    world = SyntheticWorld(seed=seed, admin_tree=build_admin_tree(rng))
    n = 0
    for _ in range(gpani):
        n += 1
        world.rows.append(make_gpani(rng, random_geo(rng, world.admin_tree), n))
    for _ in range(wpms):
        n += 1
        world.rows.append(make_wpms(rng, random_geo(rng, world.admin_tree), n))
    for _ in range(aqua):
        n += 1
        periods = sorted(rng.sample(["2024", "2025", "2026_dry", "2026_wet"], rng.randint(1, 3)),
                         key=lambda p: ["2024", "2025", "2026_dry", "2026_wet"].index(p))
        world.rows.append(make_aqua(rng, random_geo(rng, world.admin_tree), n, periods))
    for _ in range(borelog):
        n += 1
        world.rows.append(make_borelog(rng, random_geo(rng, world.admin_tree), n))
    return world


def _row_values(row: dict[str, Any]) -> list[Any]:
    out = []
    for name in WAREHOUSE_HEADERS:
        v = row.get(name)
        if v is not None and name in STRING_COLUMNS and not isinstance(v, str):
            v = str(v)
        out.append(v)
    return out


def write_world_warehouse(world: SyntheticWorld, path: str) -> None:
    rows = world.sorted_rows()
    spec = SheetSpec("FLAT_WASH_WAREHOUSE", WAREHOUSE_HEADERS, (_row_values(r) for r in rows),
                     header_styles=header_styles(), data_styles=data_styles(), widths=widths(), freeze=(3, 1),
                     autofilter=True, row_attrs=' ht="18" customHeight="1"', header_row_attrs=' ht="37" customHeight="1"')
    write_workbook(path, [spec], styles_xml=WAREHOUSE_STYLES_XML, row_counts=[len(rows) + 1])


def write_synthetic_warehouse(path: str, rows: int = 600_000, columns: int = 196, seed: int = 7,
                              progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Streaming generator for large synthetic warehouses (scale tests).

    Rows are emitted already grouped by synthetic administrative area, mirroring
    the real warehouse's geographic ordering. Extra columns beyond 196 are padded
    as ``synthetic_extra_NNN``.
    """
    rng = random.Random(seed)
    tree = build_admin_tree(rng, districts=8, upazilas=6, unions=6, villages=8)
    headers = list(WAREHOUSE_HEADERS) + [f"synthetic_extra_{i:03d}" for i in range(max(0, columns - 196))]
    keys = []
    for div in sorted(tree):
        for d in sorted(tree[div]):
            for u in sorted(tree[div][d]):
                for un in sorted(tree[div][d][u]):
                    for v in tree[div][d][u][un]:
                        keys.append((div, d, u, un, v))
    per_key = max(1, rows // len(keys) + 1)

    def gen() -> Iterator[list[Any]]:
        n = 0
        for div, d, u, un, v in keys:
            la0, la1, lo0, lo1, _bn = DIVISIONS[div]
            for _ in range(per_key):
                if n >= rows:
                    return
                n += 1
                geo = Geo(div, d, u, un, v, round(rng.uniform(la0, la1), 6), round(rng.uniform(lo0, lo1), 6))
                r = rng.random()
                if r < 0.92:
                    row = make_gpani(rng, geo, n)
                elif r < 0.975:
                    row = make_wpms(rng, geo, n)
                elif r < 0.997:
                    row = make_aqua(rng, geo, n, ["2025", "2026_wet"] if r < 0.99 else ["2026_dry"])
                else:
                    row = make_borelog(rng, geo, n)
                if progress and n % 20000 == 0:
                    progress(n / rows)
                values = _row_values(row)
                if len(headers) > 196:
                    values += [None] * (len(headers) - 196)
                yield values

    hs = header_styles() + ["6"] * (len(headers) - 196)
    ds = data_styles() + ["6"] * (len(headers) - 196)
    spec = SheetSpec("FLAT_WASH_WAREHOUSE", headers, gen(), header_styles=hs, data_styles=ds, freeze=(3, 1),
                     autofilter=True, row_attrs=' ht="18" customHeight="1"', header_row_attrs=' ht="37" customHeight="1"',
                     widths={i + 1: 24.71 for i in range(len(headers))})
    write_workbook(path, [spec], styles_xml=WAREHOUSE_STYLES_XML, row_counts=[rows + 1], compresslevel=1)
    if progress:
        progress(1.0)
    return {"rows": rows, "columns": len(headers), "admin_groups": len(keys), "headers": headers}


# ---------------------------------------------------------------------------
# Synthetic source exports
# ---------------------------------------------------------------------------

AQUA_SURVEY_HEADERS = ["SL", "Survey Year", "Ground Water Resource", "Latitude", "Longitude", "Type of Tubewell",
                       "Depth of Tubewell", "Division", "District", "Upazila", "Union", "Water Table (m)",
                       "Arsenic (As)", "Iron (Fe)", "Chloride (Cl)"]
WPMS_HEADERS = ["SL No", "Package", "Project", "Division", "District", "Upazila", "Union", "Village", "GO Code",
                "Caretaker Name", "Mobile Number", "Type", "Total Depth (Meter)", "Water Quality", "Installation Date",
                "Platform Construction Date", "Current Status", "GPS Latitude", "GPS Longitude", "Created At"]
GPANI_HEADERS = ["sl", "geo code", "Division", "District", "Upazila", "Union", "Village", "Latitude", "Longitude",
                 "Water Point Depth", "Water Quality Of Iron(Fe)", "Water Quality Of Arsenic(As)",
                 "Water Quality Of Chloride(CL-)", "Water Quality Of Manganese(Mn)", "W/P Type", "Caretaker Name"]
BORE_TABS = {
    "General Information": ["ID", "Project Name", "Owner/Caretaker Name", "TW ID No.", "Latitude", "Longitude", "Division",
                            "District", "Upazila", "Union/Pouroshova", "Village/Mahalla", "Drilling Started",
                            "Drilling Ended", "Drilling Method", "Drilling Depth", "Well Depth", "TW Type"],
    "Well Fixtures": ["ID", "Latitude", "Longitude", "Top Casing Pipe", "Blind Pipe", "Screen (Filter)", "Sand Trap",
                      "Depth of Logging"],
    "Water Quality": ["ID", "Latitude", "Longitude", "Lab Name", "Sampling Date", "Analysis Date", "EC", "pH", "Arsenic",
                      "Chloride", "Iron", "Manganese"],
    "Lithology & Well Description": ["ID", "Latitude", "Longitude", "From", "To", "Color Name", "Color Code",
                                     "Lithology Type", "Lithology Code", "Remarks"],
    "Surface Water Information": ["ID", "The names of the flowing rivers",
                                  "The water level of the river mentioned during the dry season",
                                  "Opinion on water quality"],
}


def write_table(path: str, headers: list[str], rows: list[list[Any]], sheet: str = "Sheet1") -> None:
    if path.lower().endswith(".csv"):
        import csv  # noqa: PLC0415

        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            w.writerows([["" if v is None else v for v in r] for r in rows])
        return
    write_workbook(path, [SheetSpec(sheet, headers, rows)])


def write_borelog_workbook(path: str, tabs: dict[str, list[list[Any]]]) -> None:
    write_workbook(path, [SheetSpec(name, BORE_TABS[name], tabs.get(name, [])) for name in BORE_TABS])


__all__ = ["WAREHOUSE_HEADERS", "build_world", "write_world_warehouse", "write_synthetic_warehouse", "write_table",
           "write_borelog_workbook", "AQUA_SURVEY_HEADERS", "WPMS_HEADERS", "GPANI_HEADERS", "BORE_TABS", "random_geo",
           "col_letter"]
