"""Build the public demo bundle by running the *real* engine on synthetic data.

Everything in the bundle is synthetic (``SYN`` identifiers, generated place names,
random coordinates and measurements). The JSON files mirror the local API's
response shapes, so the web app renders demo and production data with the same
code. No absolute paths, usernames or real records are written.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
from collections.abc import Callable
from typing import Any

from .config import Settings
from .engine import Engine, plan_summary
from .planner import PreviewInput
from .synthetic import (
    AQUA_SURVEY_HEADERS,
    DIVISIONS,
    GPANI_HEADERS,
    WPMS_HEADERS,
    build_world,
    random_geo,
    write_borelog_workbook,
    write_table,
    write_world_warehouse,
)

ACTOR = "demo-operator"


def _survey_row(serial: int, period: str, row: dict[str, Any] | None, rng: random.Random, **override: Any) -> list[Any]:
    base: dict[str, Any] = {h: None for h in AQUA_SURVEY_HEADERS}
    base.update({"SL": serial, "Survey Year": period, "Water Table (m)": f"{rng.uniform(2.5, 11):.2f}",
                 "Arsenic (As)": f"{rng.uniform(0.001, 0.08):.3f}", "Iron (Fe)": f"{rng.uniform(0.05, 3.5):.3f}",
                 "Chloride (Cl)": str(rng.randint(12, 420))})
    if row is not None:
        base.update({"Ground Water Resource": row["aqua_gwc_id"], "Latitude": row["latitude"], "Longitude": row["longitude"],
                     "Type of Tubewell": row["aqua_well_type"], "Depth of Tubewell": row["aqua_well_depth_m"],
                     "Division": row["division"].title(), "District": row["district"].title(),
                     "Upazila": row["upazila"].title(), "Union": row["union_name"].title()})
    base.update(override)
    return [base[h] for h in AQUA_SURVEY_HEADERS]


def _new_well(serial: int, period: str, rid: str, world: Any, rng: random.Random, **override: Any) -> list[Any]:
    geo = random_geo(rng, world.admin_tree)
    return _survey_row(serial, period, None, rng, **{
        "Ground Water Resource": rid, "Latitude": round(geo.lat, 6), "Longitude": round(geo.lon, 6),
        "Type of Tubewell": rng.choice(["Deep", "Shallow", "Moderately Deep"]), "Depth of Tubewell": str(rng.randint(25, 310)),
        "Division": geo.division.title(), "District": geo.district.title(), "Upazila": geo.upazila.title(),
        "Union": geo.union.title(), **override})


def _wpms_row(serial: int, row: dict[str, Any], **override: Any) -> list[Any]:
    adm = json.loads(row["wpms_admin_names_raw"])
    values = {"SL No": serial, "Package": "SYN-PKG-1", "Project": "Synthetic water supply project", "Division": adm["Division"],
              "District": row["district"].title(), "Upazila": row["upazila"].title(), "Union": row["union_name"],
              "Village": row["village_name"], "GO Code": row["wpms_go_code"], "Caretaker Name": "Demo Person",
              "Mobile Number": "01000000000", "Type": row["wpms_facility_type_raw"], "Total Depth (Meter)": row["wpms_total_depth_raw"],
              "Water Quality": row["wpms_water_quality_raw"], "Installation Date": row["wpms_installation_date"],
              "Platform Construction Date": "", "Current Status": row["wpms_project_status_raw"],
              "GPS Latitude": row["wpms_latitude_raw"], "GPS Longitude": row["wpms_longitude_raw"],
              "Created At": row["wpms_created_at"]}
    values.update(override)
    return [values[h] for h in WPMS_HEADERS]


def _gpani_row(serial: int, row: dict[str, Any], **override: Any) -> list[Any]:
    values = {"sl": serial, "geo code": row["gpani_source_geocode"], "Division": row["division"], "District": row["district"],
              "Upazila": row["upazila"], "Union": row["union_name"], "Village": row["village_name"], "Latitude": row["latitude"],
              "Longitude": row["longitude"], "Water Point Depth": row["gpani_tubewell_depth_raw"],
              "Water Quality Of Iron(Fe)": row["gpani_iron_raw"], "Water Quality Of Arsenic(As)": row["gpani_arsenic_raw"],
              "Water Quality Of Chloride(CL-)": row["gpani_chloride_raw"], "Water Quality Of Manganese(Mn)": "",
              "W/P Type": row["facility_type"], "Caretaker Name": "Demo Person"}
    values.update(override)
    return [values[h] for h in GPANI_HEADERS]


def _bore_general(row: dict[str, Any], **override: Any) -> list[Any]:
    values = [row["borelog_id"], "SYN-PROJ", "Demo Person", row["borelog_tw_id_no"], row["borelog_latitude_raw"],
              row["borelog_longitude_raw"], row["division"].title(), row["district"].title(), row["upazila"].title(),
              row["union_name"].title(), "", "2024-01-08", "2024-01-11", "Rotary", row["borelog_drilling_depth_raw"],
              row["borelog_well_depth_raw"], row["borelog_tubewell_type"]]
    keys = ["ID", "Project Name", "Owner/Caretaker Name", "TW ID No.", "Latitude", "Longitude", "Division", "District",
            "Upazila", "Union/Pouroshova", "Village/Mahalla", "Drilling Started", "Drilling Ended", "Drilling Method",
            "Drilling Depth", "Well Depth", "TW Type"]
    for k, v in override.items():
        values[keys.index(k)] = v
    return values


def _layers(row: dict[str, Any], extra: list[list[Any]] | None = None, change_first: bool = False) -> list[list[Any]]:
    out = []
    for i, layer in enumerate(json.loads(row["borelog_lithology_profile_raw"])):
        lith = "Gravel (Gr)" if (change_first and i == 0) else layer["lithology"]
        out.append([row["borelog_id"], "", "", layer["from"], layer["to"], layer["color"], layer["color_code"], lith,
                    layer["lithology_code"], ""])
    for e in extra or []:
        out.append([row["borelog_id"], "", "", *e])
    return out


def build_sources(world: Any, folder: str, seed: int) -> dict[str, list[str]]:
    rng = random.Random(seed)
    aqua = [r for r in world.rows if r.get("aqua_gwc_id")]
    wpms = [r for r in world.rows if r.get("wpms_go_code")]
    gpani = [r for r in world.rows if r.get("gpani_source_geocode")]
    bores = [r for r in world.rows if r.get("borelog_id")]
    files: dict[str, list[str]] = {"batch1": [], "batch2": []}

    # ---- batch 1 (applied → v001): AquaWatch 2027 dry season + WPMS update -------------
    dry = [_survey_row(i + 1, "2027 (Dry Season)", w, rng) for i, w in enumerate(aqua[:120])]
    dry += [_new_well(200 + i, "2027 (Dry Season)", f"SYN-N{i:04d}", world, rng) for i in range(12)]
    dry += [dry[3][:], dry[10][:]]  # exact duplicates
    dry += [_survey_row(300, "2027 (Dry Season)", None, rng, **{"Ground Water Resource": None}),
            _survey_row(301, "2027 (Monsoon)", aqua[130], rng)]
    p = os.path.join(folder, "aquawatch_survey_2027_dry.xlsx")
    write_table(p, AQUA_SURVEY_HEADERS, dry, "Survey Data")
    files["batch1"].append(p)
    wp_rows = [_wpms_row(i + 1, r) for i, r in enumerate(wpms[:25])]
    for i in range(4):
        wp_rows[i] = _wpms_row(i + 1, wpms[i], **{"Current Status": "হস্তান্তর"})
    for i in range(6):
        geo = random_geo(rng, world.admin_tree)
        base = dict(wpms[30 + i])
        base.update({"wpms_go_code": f"98{900000 + i:021d}", "wpms_latitude_raw": f"{geo.lat:.6f}",
                     "wpms_longitude_raw": f"{geo.lon:.6f}", "district": geo.district, "upazila": geo.upazila,
                     "union_name": geo.union, "village_name": geo.village,
                     "wpms_admin_names_raw": json.dumps({"Division": DIVISIONS[geo.division][4]}, ensure_ascii=False)})
        wp_rows.append(_wpms_row(100 + i, base))
    wp_rows.append(_wpms_row(200, wpms[40], **{"GO Code": ""}))
    changed_metric = next(r for r in aqua if "aqua_2026_wet_arsenic_raw" in r)
    p = os.path.join(folder, "wpms_tubewell_list_update.xlsx")
    write_table(p, WPMS_HEADERS, wp_rows, "tubewell_final")
    files["batch1"].append(p)
    corr1 = [_survey_row(1, "2026 (Wet Season)", changed_metric, rng, **{
        "Arsenic (As)": "0.250", "Water Table (m)": str(changed_metric["aqua_2026_wet_water_table_m"]),
        "Iron (Fe)": str(changed_metric["aqua_2026_wet_iron_raw"]), "Chloride (Cl)": str(changed_metric["aqua_2026_wet_chloride_raw"])})]
    p = os.path.join(folder, "aquawatch_2026_wet_recheck.csv")
    write_table(p, AQUA_SURVEY_HEADERS, corr1)
    files["batch1"].append(p)

    # ---- batch 2 (left for review): wet season, corrections, bore logs, GPANI --------
    wet = [_survey_row(i + 1, "2027 (Wet Season)", w, rng) for i, w in enumerate(aqua[30:120])]
    wet += [_new_well(400 + i, "2027 (Wet Season)", f"SYN-W{i:04d}", world, rng) for i in range(8)]
    for i, g in enumerate(gpani[:4]):
        gtype = "Deep" if "Deep" in g["facility_type"] else "Shallow"
        wet.append(_survey_row(500 + i, "2027 (Wet Season)", None, rng, **{
            "Ground Water Resource": f"SYN-C{i:04d}", "Latitude": g["latitude"], "Longitude": g["longitude"],
            "Type of Tubewell": gtype, "Division": g["division"].title(), "District": g["district"].title(),
            "Upazila": g["upazila"].title(), "Union": g["union_name"].title()}))
    wet += [wet[5][:], wet[6][:]]
    conflict = wet[7][:]
    conflict[AQUA_SURVEY_HEADERS.index("Arsenic (As)")] = "0.410"
    wet.append(conflict)
    wet += [_survey_row(600, "2027 (Wet Season)", None, rng, **{"Ground Water Resource": "NO GROUND WATER RESOURCE",
                                                                 "Arsenic (As)": None, "Iron (Fe)": None,
                                                                 "Chloride (Cl)": None, "Water Table (m)": None}),
            _survey_row(601, "", aqua[131], rng)]
    p = os.path.join(folder, "aquawatch_survey_2027_wet.xlsx")
    write_table(p, AQUA_SURVEY_HEADERS, wet, "Survey Data")
    files["batch2"].append(p)
    with_wet = [r for r in aqua if "aqua_2026_wet_arsenic_raw" in r and r is not changed_metric]
    corr = []
    for i, w in enumerate(with_wet[:10]):
        values = {"Water Table (m)": str(w["aqua_2026_wet_water_table_m"]), "Iron (Fe)": str(w["aqua_2026_wet_iron_raw"]),
                  "Chloride (Cl)": str(w["aqua_2026_wet_chloride_raw"]), "Arsenic (As)": str(w["aqua_2026_wet_arsenic_raw"])}
        if i < 6:
            values["Arsenic (As)"] = f"{float(w['aqua_2026_wet_arsenic_raw']) + 0.05:.3f}"
        corr.append(_survey_row(i + 1, "2026 (Wet Season)", w, rng, **values))
    p = os.path.join(folder, "aquawatch_2026_wet_corrections.csv")
    write_table(p, AQUA_SURVEY_HEADERS, corr)
    files["batch2"].append(p)
    general, litho, surface = [], [], []
    for b in bores[:4]:
        depth_to = json.loads(b["borelog_lithology_profile_raw"])[-1]["to"]
        top = int(str(depth_to).split()[0])
        general.append(_bore_general(b))
        litho += _layers(b, [[f"{top} m", f"{top + 6} m", "Grey", "C 1", "Coarse Sand (CS)", "S 3", ""]])
    for b in bores[4:6]:
        general.append(_bore_general(b))
        litho += _layers(b, change_first=True)
    for i in range(3):
        geo = random_geo(rng, world.admin_tree)
        bid = f"SYNBL-NEW-{i:03d}"
        general.append([bid, "SYN-PROJ", "Demo Person", f"97{800000 + i:021d}", f"{geo.lon:.6f}", f"{geo.lat:.6f}",
                        geo.division.title(), geo.district.title(), geo.upazila.title(), geo.union.title(), geo.village,
                        "2025-02-01", "2025-02-03", "Water Jet", "210 m", "205 m", "Deep"])
        litho += [[bid, "", "", "0 m", "6 m", "Grey", "C 1", "Clay (Cl)", "S 1", ""],
                  [bid, "", "", "6 m", "18 m", "Grey", "C 2", "Fine Sand (FS)", "S 2", ""]]
    g_ok = next(g for g in gpani[10:] if "Deep" in g["facility_type"])
    depth = int(g_ok["gpani_tubewell_depth_raw"])
    general.append(["SYNBL-XS-001", "SYN-PROJ", "Demo Person", g_ok["gpani_source_geocode"], f"{g_ok['longitude']:.6f}",
                    f"{g_ok['latitude']:.6f}", g_ok["division"].title(), g_ok["district"].title(), g_ok["upazila"].title(),
                    g_ok["union_name"].title(), "", "2024-03-01", "2024-03-04", "Rotary", f"{depth + 4} m", f"{depth} m", "Deep"])
    g_far = gpani[20]
    general.append(["SYNBL-XS-002", "SYN-PROJ", "Demo Person", g_far["gpani_source_geocode"], f"{g_far['longitude'] + 0.4:.6f}",
                    f"{g_far['latitude'] - 0.4:.6f}", g_far["division"].title(), g_far["district"].title(),
                    g_far["upazila"].title(), g_far["union_name"].title(), "", "2024-03-01", "2024-03-04", "Rotary",
                    "200 m", "196 m", "Deep"])
    test_geo = random_geo(rng, world.admin_tree)
    general.append(["SYNBL-TEST-01", "SYN-PROJ", "Demo Person", "", f"{test_geo.lon:.6f}", f"{test_geo.lat:.6f}",
                    test_geo.division.title(), test_geo.district.title(), test_geo.upazila.title(), test_geo.union.title(),
                    "", "2025-01-01", "2025-01-01", "Rotary", "12345 m", "12345 m", "Shallow"])
    surface.append([bores[0]["borelog_id"], "Synthetic River", "Little amount", "Good"])
    p = os.path.join(folder, "borelog_workbook_update.xlsx")
    write_borelog_workbook(p, {"General Information": general, "Lithology & Well Description": litho,
                               "Surface Water Information": surface})
    files["batch2"].append(p)
    gp_rows = [_gpani_row(i + 1, g) for i, g in enumerate(gpani[100:130])]
    for i in range(3):
        geo = random_geo(rng, world.admin_tree)
        base = dict(gpani[200 + i])
        base.update({"gpani_source_geocode": f"99{990000 + i:021d}", "latitude": geo.lat, "longitude": geo.lon,
                     "division": geo.division, "district": geo.district, "upazila": geo.upazila, "union_name": geo.union,
                     "village_name": geo.village})
        gp_rows.append(_gpani_row(100 + i, base))
    for i in range(2):
        base = dict(gpani[300 + i])
        base["latitude"] = round(base["latitude"] + 0.21, 6)
        gp_rows.append(_gpani_row(200 + i, base))
    p = os.path.join(folder, "gpani_tubewell_report.xlsx")
    write_table(p, GPANI_HEADERS, gp_rows, "report")
    files["batch2"].append(p)
    return files


def _dump(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), default=str)


def build_demo_bundle(out_dir: str, seed: int = 2026, log: Callable[[str], None] = print) -> dict[str, Any]:
    out_dir = os.path.abspath(out_dir)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    with tempfile.TemporaryDirectory(prefix="wash-demo-") as tmp:
        world = build_world(seed=seed)
        wh = os.path.join(tmp, "synthetic_warehouse.xlsx")
        write_world_warehouse(world, wh)
        state = os.path.join(tmp, "state")
        eng = Engine(Settings(state_dir=state), state)
        eng.init(wh, actor=ACTOR)
        log(f"synthetic baseline: {len(world.rows)} facilities")
        sources = build_sources(world, tmp, seed)
        plan1 = eng.preview([PreviewInput(p) for p in sources["batch1"]], actor=ACTOR)
        for item in plan1.review_items:
            if item.classification.value == "NEW_SOURCE_METADATA":
                eng.decide(plan1.plan_id, item.item_id, "ACCEPT_NEW_VALUES", "Handover confirmed by the synthetic field office", ACTOR)
            elif item.classification.value == "UPDATED_MEASUREMENT":
                eng.decide(plan1.plan_id, item.item_id, "DEFER", "Waiting for the laboratory re-test certificate", ACTOR)
        eng.approve(plan1.plan_id, ACTOR, note="Demo batch 1")
        result = eng.apply(plan1.plan_id, ACTOR)
        log(f"batch 1 applied → v{result['new_version']:03d} ({result['counts']['added_facilities']} facilities added)")
        plan2 = eng.preview([PreviewInput(p) for p in sources["batch2"]], actor=ACTOR)
        dup = next((i for i in plan2.review_items if i.classification.value == "POTENTIAL_DUPLICATE"), None)
        if dup is not None:
            idx = next(i for i, o in enumerate(dup.options) if o.decision == "KEEP_SEPARATE")
            eng.decide(plan2.plan_id, dup.item_id, "KEEP_SEPARATE", "Different tubewell sharing a project GPS point", ACTOR, idx)
        plan2 = eng.plan(plan2.plan_id)
        log(f"batch 2 previewed: {len(plan2.review_items)} review items")

        # ---- export ------------------------------------------------------------
        status = eng.status()
        status["mode"] = "demo"
        _dump(os.path.join(out_dir, "status.json"), status)
        _dump(os.path.join(out_dir, "versions.json"), eng.history())
        _dump(os.path.join(out_dir, "pending.json"), eng.store.pending())
        plans = [eng.plan(pid) for pid in eng.store.list_plans()]
        _dump(os.path.join(out_dir, "plans.json"), [plan_summary(p) for p in plans])
        for p in plans:
            records = {r.rec_id: r for r in p.records}
            detail = json.loads(p.model_dump_json(exclude={"records", "review_items"}))
            detail["audit_dir"] = None
            detail["review_counts"] = {"total": len(p.review_items), "undecided": len(p.undecided)}
            detail["record_count"] = len(p.records)
            _dump(os.path.join(out_dir, "plans", f"{p.plan_id}.json"), detail)
            _dump(os.path.join(out_dir, "plans", f"{p.plan_id}.records.json"), [json.loads(r.model_dump_json()) for r in p.records])
            _dump(os.path.join(out_dir, "plans", f"{p.plan_id}.items.json"), [
                {**json.loads(i.model_dump_json()), "record": json.loads(records[i.rec_id].model_dump_json()) if i.rec_id in records else None}
                for i in p.review_items])
            for stage in ("preview", "apply"):
                src = eng.store.p("runs", p.plan_id, stage)
                if os.path.isdir(src):
                    dst = os.path.join(out_dir, "audit", p.plan_id, stage)
                    os.makedirs(dst, exist_ok=True)
                    for name in os.listdir(src):
                        if name.endswith((".csv", ".json", ".html")) and name != "fingerprints.jsonl":
                            shutil.copyfile(os.path.join(src, name), os.path.join(dst, name))
        idx = eng.index()
        try:
            _dump(os.path.join(out_dir, "schema.json"), {"headers": idx.headers, "columns": idx.column_profiles(),
                                                         "rows": idx.n_rows, "periods": idx.periods(), "sheet": idx.sheet_name})
            _dump(os.path.join(out_dir, "quality.json"), {"quality": idx.quality(), "columns": idx.column_profiles(),
                                                          "rows": idx.n_rows})
            _dump(os.path.join(out_dir, "facets.json"), idx.facets())
            rows = []
            for r in idx.conn.execute("SELECT row_num, cells FROM rows ORDER BY row_num"):
                rows.append({"row": r["row_num"], "values": idx._decode(r["cells"])})
            _dump(os.path.join(out_dir, "warehouse.json"), {"headers": idx.headers, "rows": rows})
        finally:
            idx.close()
        settings = eng.settings.public_dict()
        settings.pop("state_dir", None)
        _dump(os.path.join(out_dir, "settings.json"), settings)
        sample_dir = os.path.join(out_dir, "samples")
        os.makedirs(sample_dir)
        samples = []
        descriptions = {
            "aquawatch_survey_2027_wet.xlsx": ("AquaWatch 2027 wet-season survey", "New season, new wells, GPS coincidences, duplicates and invalid rows"),
            "aquawatch_2026_wet_corrections.csv": ("AquaWatch 2026 wet corrections", "Changed and unchanged measurements for existing wells"),
            "borelog_workbook_update.xlsx": ("Bore-log workbook (5 tabs)", "New layers, changed layers, new boreholes and cross-source matches"),
            "gpani_tubewell_report.xlsx": ("GPANI tubewell report", "Unchanged rows, new geocodes and reused geocodes"),
            "aquawatch_survey_2027_dry.xlsx": ("AquaWatch 2027 dry-season survey", "Applied in demo version v001"),
            "wpms_tubewell_list_update.xlsx": ("WPMS tubewell list", "Status updates and new GO codes (applied in v001)"),
        }
        for path in sources["batch2"] + sources["batch1"]:
            name = os.path.basename(path)
            shutil.copyfile(path, os.path.join(sample_dir, name))
            title, desc = descriptions.get(name, (name, ""))
            samples.append({"file": name, "title": title, "description": desc})
        _dump(os.path.join(out_dir, "samples.json"), samples)
        _dump(os.path.join(out_dir, "manifest.json"), {"synthetic": True, "seed": seed, "engine_version": status["engine_version"],
                                                       "note": "All data in this folder is synthetic and was produced by the real engine."})
    return {"plans": len(plans), "rows": len(rows), "samples": len(samples)}
