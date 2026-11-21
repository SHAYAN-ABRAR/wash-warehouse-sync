"""End-to-end synchronization scenarios on small synthetic warehouses.

Covers the required integration cases: initial creation, new facility, new annual
and seasonal surveys, missing-value fills, conflicting measurements, geological
profile updates, existing observations, idempotent reruns, false GPS-only matches,
workbook integrity, preservation of unrelated cells, single worksheet, interrupted
export recovery and geographic row ordering.
"""

from __future__ import annotations

import json
import os

import openpyxl
import pytest
from wash_sync.engine import EngineError
from wash_sync.records import ChangeClass
from wash_sync.synthetic import GPANI_HEADERS, WPMS_HEADERS, write_borelog_workbook, write_table
from wash_sync.xlsx.writer import OperationCancelled

from tests.conftest import aqua_row, aqua_rows, current_path, preview, table, write_survey

DRY = "2027 (Dry Season)"


def classes(plan) -> dict[str, int]:
    return {k: v for k, v in plan.summary["by_classification"].items() if v}


def apply_all(eng, plan, **kw):
    eng.approve(plan.plan_id, "pytest")
    return eng.apply(plan.plan_id, "pytest", **kw)


# 1 -------------------------------------------------------------------------
def test_initial_warehouse_creation(engine, world):
    current = engine.store.current_version()
    assert current["version"] == 0 and current["kind"] == "BASELINE"
    assert current["rows"] == len(world.rows) and current["columns"] == 196
    report = engine.verify()
    assert report["ok"] and report["sha256_matches_record"]
    assert oct(os.stat(current_path(engine)).st_mode & 0o777) == "0o444"  # immutable copy


# 2 + 15 ---------------------------------------------------------------------
def test_new_facility_inserted_in_geographic_group(engine, world, tmp_path):
    anchor = next(r for r in world.rows if r.get("gpani_source_geocode"))
    new = aqua_row(1, DRY, None, **{"Ground Water Resource": "SYN-BRAND-NEW", "Latitude": anchor["latitude"] + 0.02,
                                    "Longitude": anchor["longitude"] + 0.02, "Type of Tubewell": "Shallow",
                                    "Division": anchor["division"].title(), "District": anchor["district"].title(),
                                    "Upazila": anchor["upazila"].title(), "Union": anchor["union_name"].title()})
    plan = preview(engine, write_survey(tmp_path / "s.xlsx", [new]))
    assert classes(plan) == {"NEW_FACILITY": 1}
    result = apply_all(engine, plan)
    assert result["counts"]["added_facilities"] == 1
    headers, rows = table(current_path(engine))
    idx = next(i for i, r in enumerate(rows) if r.get("aqua_gwc_id") == "SYN-BRAND-NEW")
    inserted = rows[idx]
    assert inserted["candidate_id"] == "AQUA2027DRY:SYN-BRAND-NEW"
    assert inserted["division"] == anchor["division"] and inserted["union_name"] == anchor["union_name"]
    neighbours = [rows[idx - 1], rows[idx + 1] if idx + 1 < len(rows) else rows[idx - 1]]
    assert any(n.get("union_name") == anchor["union_name"] for n in neighbours)  # grouped, not appended at the bottom
    assert idx < len(rows) - 1 or rows[-2].get("union_name") == anchor["union_name"]


# 3 ---------------------------------------------------------------------------
def test_new_annual_survey_creates_annual_columns(engine, world, tmp_path):
    wells = aqua_rows(world)[:5]
    plan = preview(engine, write_survey(tmp_path / "s.xlsx", [aqua_row(i, "2027", w) for i, w in enumerate(wells)]))
    assert classes(plan) == {"NEW_OBSERVATION": 5}
    assert [c.name for c in plan.new_columns][:2] == ["aqua_2027_arsenic_raw", "aqua_2027_water_table_m"]
    apply_all(engine, plan)
    headers, rows = table(current_path(engine))
    assert headers.index("aqua_2027_source_row") < headers.index("borelog_id")
    assert len(rows) == len(world.rows)  # no facility rows added for existing wells
    for w in wells:
        row = next(r for r in rows if r.get("aqua_gwc_id") == w["aqua_gwc_id"])
        assert row["aqua_2027_water_table_m"] == 5.5
        assert row["record_sources"].endswith("AQUA_2027")


# 4 ---------------------------------------------------------------------------
def test_new_seasonal_surveys_stay_separate(engine, world, tmp_path):
    w = aqua_rows(world)[0]
    dry = write_survey(tmp_path / "dry.xlsx", [aqua_row(1, "2027 (Dry Season)", w, **{"Water Table (m)": "4.0"})])
    wet = write_survey(tmp_path / "wet.xlsx", [aqua_row(1, "2027 (Wet Season)", w, **{"Water Table (m)": "2.0"})])
    plan = preview(engine, dry, wet)
    assert classes(plan) == {"NEW_OBSERVATION": 2}
    apply_all(engine, plan)
    _h, rows = table(current_path(engine))
    row = next(r for r in rows if r.get("aqua_gwc_id") == w["aqua_gwc_id"])
    assert row["aqua_2027_dry_water_table_m"] == 4.0 and row["aqua_2027_wet_water_table_m"] == 2.0
    tokens = row["record_sources"].split(";")
    assert "AQUA_2027_DRY" in tokens and "AQUA_2027_WET" in tokens


def test_2025_annual_and_2025_wet_are_distinct_periods(engine, world, tmp_path):
    w = next(r for r in aqua_rows(world) if "aqua_2025_wet_arsenic_raw" not in r)
    plan = preview(engine, write_survey(tmp_path / "w.xlsx", [aqua_row(1, "2025 (Wet Season)", w)]))
    rec = next(r for r in plan.records if r.source_record_id == w["aqua_gwc_id"])
    assert rec.period == "2025_wet"
    cells = {c.column for o in rec.operations for c in o.cells}
    assert "aqua_2025_wet_arsenic_raw" in cells and "aqua_2025_arsenic_raw" not in cells


# 5 ---------------------------------------------------------------------------
def test_fill_missing_measurement(engine, world, tmp_path):
    w = next(r for r in aqua_rows(world) if "aqua_2026_wet_arsenic_raw" in r)
    survey = aqua_row(1, "2026 (Wet Season)", w, **{
        "Arsenic (As)": str(w["aqua_2026_wet_arsenic_raw"]), "Water Table (m)": str(w["aqua_2026_wet_water_table_m"]),
        "Iron (Fe)": str(w["aqua_2026_wet_iron_raw"]), "Chloride (Cl)": str(w["aqua_2026_wet_chloride_raw"])})
    # blank the existing iron value in a modified baseline, then sync the same survey
    plan0 = preview(engine, write_survey(tmp_path / "same.xlsx", [survey]))
    assert classes(plan0) == {"UNCHANGED": 1}
    _blank_cell(engine, w["candidate_id"], "aqua_2026_wet_iron_raw", tmp_path)
    plan = preview(engine, write_survey(tmp_path / "again.xlsx", [survey]))
    assert classes(plan) == {"FILLED_MISSING_VALUE": 1}
    cells = [c for o in plan.records[0].operations for c in o.cells]
    assert {c.column for c in cells if c.action == "FILL"} >= {"aqua_2026_wet_iron_raw"}
    assert all(c.action != "SET" for c in cells)


def _blank_cell(engine, candidate_id, column, tmp_path):
    """Test helper: register a modified copy of the current warehouse as a new baseline."""
    path = current_path(engine)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    col = headers.index(column) + 1
    for row in ws.iter_rows(min_row=2):
        if row[0].value == candidate_id:
            row[col - 1].value = None
    out = tmp_path / "modified.xlsx"
    wb.save(out)
    engine.init(str(out), actor="pytest", force=True)


# 6 ---------------------------------------------------------------------------
def test_conflicting_measurement_requires_review(engine, world, tmp_path):
    w = next(r for r in aqua_rows(world) if "aqua_2026_wet_arsenic_raw" in r)
    changed = aqua_row(1, "2026 (Wet Season)", w, **{
        "Arsenic (As)": "0.999", "Water Table (m)": str(w["aqua_2026_wet_water_table_m"]),
        "Iron (Fe)": str(w["aqua_2026_wet_iron_raw"]), "Chloride (Cl)": str(w["aqua_2026_wet_chloride_raw"])})
    plan = preview(engine, write_survey(tmp_path / "c.xlsx", [changed]))
    assert classes(plan) == {"UPDATED_MEASUREMENT": 1}
    item = plan.review_items[0]
    assert [o.decision for o in item.options][:2] == ["ACCEPT_NEW_VALUES", "KEEP_EXISTING"]
    assert not plan.records[0].operations  # nothing applied without a decision
    result = apply_all(engine, plan)
    assert result.get("new_version") is None  # undecided → deferred, warehouse unchanged
    assert engine.store.pending()[0]["item_id"] == item.item_id
    plan2 = preview(engine, write_survey(tmp_path / "c2.xlsx", [changed]))
    item2 = plan2.review_items[0]
    with pytest.raises(EngineError, match="reason"):
        engine.decide(plan2.plan_id, item2.item_id, "ACCEPT_NEW_VALUES", "  ", "pytest")
    engine.decide(plan2.plan_id, item2.item_id, "ACCEPT_NEW_VALUES", "Lab re-test confirmed", "pytest")
    apply_all(engine, plan2)
    _h, rows = table(current_path(engine))
    assert next(r for r in rows if r.get("aqua_gwc_id") == w["aqua_gwc_id"])["aqua_2026_wet_arsenic_raw"] == 0.999
    history = os.path.join(engine.store.p("runs", plan2.plan_id, "apply"), "field_change_history.csv")
    text = open(history, encoding="utf-8-sig").read()
    assert "aqua_2026_wet_arsenic_raw" in text and "0.999" in text and str(w["aqua_2026_wet_arsenic_raw"]) in text


def test_rejected_decision_is_remembered(engine, world, tmp_path):
    w = next(r for r in aqua_rows(world) if "aqua_2026_wet_arsenic_raw" in r)
    changed = aqua_row(1, "2026 (Wet Season)", w, **{"Arsenic (As)": "0.777"})
    src = write_survey(tmp_path / "r.xlsx", [changed])
    plan = preview(engine, src)
    engine.decide(plan.plan_id, plan.review_items[0].item_id, "REJECT", "Known bad sample", "pytest")
    apply_all(engine, plan)
    plan2 = preview(engine, src)
    assert plan2.review_items[0].decision is not None and plan2.review_items[0].decision.remembered


# 7 ---------------------------------------------------------------------------
def _bore_general(row, **kw):
    base = [row["borelog_id"], "SYN-PROJ", "Owner", row["borelog_tw_id_no"], str(row["latitude"]), str(row["longitude"]),
            row["division"].title(), row["district"].title(), row["upazila"].title(), row["union_name"].title(), "",
            "2024-01-01", "2024-01-03", "Rotary", row["borelog_drilling_depth_raw"], row["borelog_well_depth_raw"],
            row["borelog_tubewell_type"]]
    for k, v in kw.items():
        base[k] = v
    return base


def _litho_rows(row, extra=None, change=False):
    layers = json.loads(row["borelog_lithology_profile_raw"])
    out = []
    for i, layer in enumerate(layers):
        lith = "Gravel" if (change and i == 0) else layer["lithology"]
        out.append([row["borelog_id"], "", "", layer["from"], layer["to"], layer["color"], layer["color_code"], lith,
                    layer["lithology_code"], ""])
    for e in extra or []:
        out.append([row["borelog_id"], "", "", *e])
    return out


def test_geological_profile_new_layers_and_changed_layers(engine, world, tmp_path):
    bore = next(r for r in world.rows if r.get("borelog_id"))
    last_to = json.loads(bore["borelog_lithology_profile_raw"])[-1]["to"]
    depth = int(last_to.split()[0])
    added = [[f"{depth} m", f"{depth + 6} m", "Grey", "C 1", "Coarse Sand (CS)", "S 1", ""]]
    src = tmp_path / "bore.xlsx"
    write_borelog_workbook(str(src), {"General Information": [_bore_general(bore)],
                                      "Lithology & Well Description": _litho_rows(bore, added),
                                      "Surface Water Information": [[bore["borelog_id"], "", "", ""]]})
    plan = preview(engine, str(src))
    rec = plan.records[0]
    assert rec.classification == ChangeClass.NEW_OBSERVATION, rec.proposed_action
    cells = {c.column: c for o in rec.operations for c in o.cells}
    assert json.loads(cells["borelog_lithology_profile_raw"].new)[-1]["lithology"] == "Coarse Sand (CS)"
    assert cells["borelog_lithology_layer_count"].new == len(json.loads(bore["borelog_lithology_profile_raw"])) + 1
    apply_all(engine, plan)
    _h, rows = table(current_path(engine))
    assert sum(1 for r in rows if r.get("borelog_id") == bore["borelog_id"]) == 1  # no second facility row
    # a modified existing layer requires review and keeps the previous profile in the audit
    write_borelog_workbook(str(tmp_path / "bore2.xlsx"), {
        "General Information": [_bore_general(bore)],
        "Lithology & Well Description": _litho_rows(bore, added, change=True)})
    plan2 = preview(engine, str(tmp_path / "bore2.xlsx"))
    assert plan2.records[0].classification == ChangeClass.UPDATED_MEASUREMENT
    accept = plan2.review_items[0].options[0].operations[0].cells
    old_profile = next(c.old for c in accept if c.column == "borelog_lithology_profile_raw")
    assert "Coarse Sand" in old_profile  # previous profile preserved as evidence


# 8 + 9 ----------------------------------------------------------------------
def test_existing_observations_and_idempotent_rerun(engine, world, tmp_path):
    wells = aqua_rows(world)[:6]
    src = write_survey(tmp_path / "s.xlsx", [aqua_row(i, DRY, w) for i, w in enumerate(wells)] + [
        aqua_row(99, DRY, None, **{"Ground Water Resource": "SYN-NEW-X", "Latitude": 25.1, "Longitude": 89.0,
                                   "Division": "Rangpur", "District": "Testdist", "Type of Tubewell": "Deep"})])
    plan = preview(engine, src)
    first = apply_all(engine, plan)
    v1 = engine.store.current_version()
    headers1, rows1 = table(current_path(engine))
    for _ in range(2):
        again = preview(engine, src)
        assert classes(again) == {"UNCHANGED": 7}
        res = engine.apply(again.plan_id, "pytest", authorize=True)
        assert res["new_version"] is None
    assert engine.store.current_version()["version"] == v1["version"]
    headers2, rows2 = table(current_path(engine))
    assert headers1 == headers2 and rows1 == rows2  # no extra rows, columns, values or counts
    assert first["counts"]["added_facilities"] == 1


def test_reordered_export_is_not_new(engine, world, tmp_path):
    wells = aqua_rows(world)[:4]
    rows = [aqua_row(i, DRY, w) for i, w in enumerate(wells)]
    apply_all(engine, preview(engine, write_survey(tmp_path / "a.xlsx", rows)))
    shuffled = [r[:] for r in reversed(rows)]
    for i, r in enumerate(shuffled):
        r[0] = i + 100  # serial numbers / row positions differ
    plan = preview(engine, write_survey(tmp_path / "b.xlsx", shuffled))
    assert classes(plan) == {"UNCHANGED": 4}


def test_within_run_duplicates(engine, world, tmp_path):
    w = aqua_rows(world)[0]
    rows = [aqua_row(1, DRY, w), aqua_row(2, DRY, w)]
    plan = preview(engine, write_survey(tmp_path / "d.xlsx", rows))
    assert classes(plan) == {"NEW_OBSERVATION": 1, "EXACT_DUPLICATE": 1}
    conflict = [aqua_row(1, DRY, w), aqua_row(2, DRY, w, **{"Arsenic (As)": "0.5"})]
    plan2 = preview(engine, write_survey(tmp_path / "e.xlsx", conflict))
    assert classes(plan2) == {"CONFLICTING_MEASUREMENT": 2}
    item = plan2.review_items[0]
    assert sum(1 for o in item.options if o.decision == "ACCEPT") == 2


# 10 --------------------------------------------------------------------------
def test_false_gps_only_match_is_not_merged(engine, world, tmp_path):
    gp = next(r for r in world.rows if r.get("gpani_source_geocode"))
    same_spot = aqua_row(1, DRY, None, **{"Ground Water Resource": "SYN-COINCIDENT", "Latitude": gp["latitude"],
                                          "Longitude": gp["longitude"], "Type of Tubewell": "Deep",
                                          "Division": gp["division"], "District": gp["district"]})
    plan = preview(engine, write_survey(tmp_path / "g.xlsx", [same_spot]))
    rec = plan.records[0]
    assert rec.classification == ChangeClass.POTENTIAL_DUPLICATE
    assert rec.match.level == "L3_GEOGRAPHIC" and "EXACT_GPS_COINCIDENCE" in rec.match.conflicts
    assert not rec.operations  # never merged automatically
    item = plan.review_items[0]
    assert item.options[item.recommended].decision == "KEEP_SEPARATE"
    engine.decide(plan.plan_id, item.item_id, "KEEP_SEPARATE", "Different well at a shared project point", "pytest")
    apply_all(engine, plan)
    _h, rows = table(current_path(engine))
    assert len(rows) == len(world.rows) + 1
    assert next(r for r in rows if r.get("candidate_id") == gp["candidate_id"]).get("aqua_gwc_id") is None
    links = engine.store.crosswalk()
    assert any(link["link_type"] == "KEEP_SEPARATE" and link["source_record_id"] == "SYN-COINCIDENT" for link in links)


def test_approved_geographic_match_writes_into_existing_row(engine, world, tmp_path):
    gp = next(r for r in world.rows if r.get("gpani_source_geocode"))
    near = aqua_row(1, DRY, None, **{"Ground Water Resource": "SYN-SAMEWELL", "Latitude": gp["latitude"] + 0.00005,
                                     "Longitude": gp["longitude"], "Division": gp["division"], "District": gp["district"]})
    plan = preview(engine, write_survey(tmp_path / "n.xlsx", [near]))
    item = plan.review_items[0]
    idx = next(i for i, o in enumerate(item.options) if o.decision == "APPROVE_MATCH")
    engine.decide(plan.plan_id, item.item_id, "APPROVE_MATCH", "Field team confirmed same tubewell", "pytest", idx)
    apply_all(engine, plan)
    _h, rows = table(current_path(engine))
    row = next(r for r in rows if r.get("candidate_id") == gp["candidate_id"])
    assert row["aqua_gwc_id"] == "SYN-SAMEWELL" and row["aqua_2027_dry_water_table_m"] == 5.5
    assert len(rows) == len(world.rows)


# 11 + 12 + 13 -----------------------------------------------------------------
def test_integrity_preservation_and_single_sheet(engine, world, tmp_path):
    base_path = current_path(engine)
    wells = aqua_rows(world)[:3]
    new = aqua_row(9, DRY, None, **{"Ground Water Resource": "SYN-P1", "Latitude": 24.5, "Longitude": 90.5,
                                    "Division": "Mymensingh", "District": "Zzz", "Type of Tubewell": "Deep"})
    plan = preview(engine, write_survey(tmp_path / "p.xlsx", [aqua_row(i, DRY, w) for i, w in enumerate(wells)] + [new]))
    result = apply_all(engine, plan)
    new_path = current_path(engine)
    report = engine.verify()
    assert report["ok"], [c for c in report["checks"] if c["status"] != "PASS"]
    wb_new = openpyxl.load_workbook(new_path)
    assert wb_new.sheetnames == ["FLAT_WASH_WAREHOUSE"]
    ws_new = wb_new.active
    assert ws_new.freeze_panes == "D2" and ws_new.auto_filter.ref == f"A1:{ws_new.dimensions.split(':')[1]}"
    ws_old = openpyxl.load_workbook(base_path).active
    h_old = [c.value for c in ws_old[1]]
    h_new = [c.value for c in ws_new[1]]
    touched = {w["candidate_id"] for w in wells}
    new_rows = {r[0].value: r for r in ws_new.iter_rows(min_row=2)}
    for old in ws_old.iter_rows(min_row=2):
        cid = old[0].value
        now = new_rows[cid]
        for i, cell in enumerate(old):
            if cid in touched and h_old[i] in ("record_sources", "quality_flags", "aqua_quality_flags"):
                continue  # intentionally appended tokens on the updated rows
            j = h_new.index(h_old[i])
            assert now[j].value == cell.value, (cid, h_old[i])
            assert now[j].style_id == cell.style_id or cid in touched
    assert result["counts"]["added_facilities"] == 1


# 14 ----------------------------------------------------------------------------
def test_interrupted_export_leaves_original_intact(engine, world, tmp_path):
    base = engine.store.current_version()
    sha_before = base["sha256"]
    plan = preview(engine, write_survey(tmp_path / "x.xlsx", [aqua_row(1, DRY, aqua_rows(world)[0])]))
    engine.approve(plan.plan_id, "pytest")
    with pytest.raises(OperationCancelled):
        engine.apply(plan.plan_id, "pytest", cancelled=lambda: True)
    assert engine.store.current_version()["version"] == base["version"]
    assert engine.verify()["sha256_matches_record"] and engine.store.current_version()["sha256"] == sha_before
    assert not os.listdir(engine.store.p("tmp"))  # partial output removed
    assert engine.recover()["interrupted_plans"] == []  # cancellation was already recorded
    # a simulated crash (journal left in "writing" with a dead pid) is cleaned by recover()
    crash_dir = engine.store.p("tmp", "job-crash")
    os.makedirs(crash_dir)
    open(os.path.join(crash_dir, "WASH_Warehouse_v001.xlsx"), "wb").write(b"partial")
    json.dump({"job_id": "job-crash", "plan_id": plan.plan_id, "status": "writing", "pid": 999999,
               "tmp": os.path.join(crash_dir, "WASH_Warehouse_v001.xlsx")},
              open(engine.store.p("jobs", "job-crash.json"), "w"))
    rec = engine.recover()
    assert rec["interrupted_plans"] == [plan.plan_id] and not os.path.exists(crash_dir)
    result = engine.apply(plan.plan_id, "pytest")  # the plan can simply be re-applied
    assert result["new_version"] == 1


# safety rules ------------------------------------------------------------------
def test_apply_requires_approval_or_authorization(engine, world, tmp_path):
    plan = preview(engine, write_survey(tmp_path / "a.xlsx", [aqua_row(1, DRY, aqua_rows(world)[0])]))
    with pytest.raises(EngineError, match="not approved"):
        engine.apply(plan.plan_id, "pytest")
    assert engine.apply(plan.plan_id, "pytest", authorize=True)["new_version"] == 1


def test_stale_plan_is_refused(engine, world, tmp_path):
    w = aqua_rows(world)
    p1 = preview(engine, write_survey(tmp_path / "1.xlsx", [aqua_row(1, DRY, w[0])]))
    p2 = preview(engine, write_survey(tmp_path / "2.xlsx", [aqua_row(1, DRY, w[1])]))
    apply_all(engine, p1)
    with pytest.raises(EngineError, match="Run a new preview|no longer current"):
        apply_all(engine, p2)


def test_decision_after_approval_requires_reapproval(engine, world, tmp_path):
    w = next(r for r in aqua_rows(world) if "aqua_2026_wet_arsenic_raw" in r)
    plan = preview(engine, write_survey(tmp_path / "c.xlsx", [aqua_row(1, "2026 (Wet Season)", w, **{"Arsenic (As)": "0.42"})]))
    engine.approve(plan.plan_id, "pytest")
    engine.decide(plan.plan_id, plan.review_items[0].item_id, "ACCEPT_NEW_VALUES", "ok", "pytest")
    assert engine.plan(plan.plan_id).status == "DRAFT"


def test_rollback_and_history(engine, world, tmp_path):
    apply_all(engine, preview(engine, write_survey(tmp_path / "1.xlsx", [aqua_row(1, DRY, aqua_rows(world)[0])])))
    assert engine.store.current_version()["version"] == 1
    engine.rollback(0, "pytest", "Testing restore")
    assert engine.store.current_version()["version"] == 0
    events = [e["event"] for e in engine.history()["events"]]
    assert events[0] == "INIT" and "APPLY" in events and events[-1] == "ROLLBACK"
    plan = preview(engine, write_survey(tmp_path / "2.xlsx", [aqua_row(1, DRY, aqua_rows(world)[0])]))
    assert classes(plan) == {"NEW_OBSERVATION": 1}  # v1's data is not in v0
    result = apply_all(engine, plan)
    assert result["new_version"] == 2 and engine.store.version(2)["previous_version"] == 0


def test_invalid_records_are_rejected_with_reasons(engine, world, tmp_path):
    rows = [aqua_row(1, DRY, None, **{"Ground Water Resource": None}),
            aqua_row(2, "2027 (Monsoon)", aqua_rows(world)[0])]
    plan = preview(engine, write_survey(tmp_path / "i.xlsx", rows))
    assert classes(plan) == {"INVALID_SOURCE_RECORD": 2}
    errs = open(os.path.join(plan.audit_dir, "validation_errors.csv"), encoding="utf-8-sig").read()
    assert "MISSING_SOURCE_ID" in errs and "SURVEY_PERIOD_PROBLEM" in errs


def test_strict_mode_escalates_warnings(engine, world, tmp_path):
    row = aqua_row(1, DRY, aqua_rows(world)[0], **{"Type of Tubewell": None})
    src = write_survey(tmp_path / "s.xlsx", [row])
    assert classes(preview(engine, src)) == {"NEW_OBSERVATION": 1}
    engine.settings.validation.mode = "strict"
    assert classes(preview(engine, src)) == {"INVALID_SOURCE_RECORD": 1}


# other sources --------------------------------------------------------------------
def test_wpms_and_gpani_sources(engine, world, tmp_path):
    wp = next(r for r in world.rows if r.get("wpms_go_code"))
    adm = json.loads(wp["wpms_admin_names_raw"])
    unchanged = [1, "P", "Proj", adm["Division"], wp["district"].title(), wp["upazila"].title(), wp["union_name"],
                 wp["village_name"], wp["wpms_go_code"], "Private", "017", wp["wpms_facility_type_raw"],
                 wp["wpms_total_depth_raw"], wp["wpms_water_quality_raw"], wp["wpms_installation_date"], "",
                 wp["wpms_project_status_raw"], wp["wpms_latitude_raw"], wp["wpms_longitude_raw"], wp["wpms_created_at"]]
    status_change = list(unchanged)
    status_change[16] = "হস্তান্তর"
    twin = list(unchanged)
    twin[8] = "98999999999999999999998"  # different GO code at the exact same point, same type
    new = list(unchanged)
    new[8] = "98999999999999999999999"
    new[17], new[18] = f"{float(wp['wpms_latitude_raw']) + 0.05:.6f}", f"{float(wp['wpms_longitude_raw']) + 0.05:.6f}"
    path = tmp_path / "wpms.xlsx"
    write_table(str(path), WPMS_HEADERS, [unchanged])
    assert classes(preview(engine, str(path))) == {"UNCHANGED": 1}
    write_table(str(path), WPMS_HEADERS, [twin])
    twin_plan = preview(engine, str(path))
    assert classes(twin_plan) == {"POTENTIAL_DUPLICATE": 1}  # exact coincidence within one system → review
    write_table(str(path), WPMS_HEADERS, [status_change, new])
    plan = preview(engine, str(path))
    assert classes(plan) == {"NEW_SOURCE_METADATA": 1, "NEW_FACILITY": 1}
    new_rec = next(r for r in plan.records if r.classification == ChangeClass.NEW_FACILITY)
    cells = {c.column: c.new for c in new_rec.operations[0].cells}
    assert cells["division"] == wp["division"]  # Bangla division mapped via verified warehouse rows
    gp = next(r for r in world.rows if r.get("gpani_source_geocode"))
    reused = [1, gp["gpani_source_geocode"], gp["division"], gp["district"], gp["upazila"], gp["union_name"],
              gp["village_name"], gp["latitude"] + 0.3, gp["longitude"], "100", "0.2", "0.01", "50", "", gp["facility_type"], "X"]
    write_table(str(tmp_path / "gp.xlsx"), GPANI_HEADERS, [reused])
    plan2 = preview(engine, str(tmp_path / "gp.xlsx"))
    rec = plan2.records[0]
    assert rec.classification == ChangeClass.CONFLICTING_IDENTITY
    assert "GEOCODE_REUSED_OR_GPS_CHANGED" in rec.match.conflicts


def test_borelog_verified_cross_source_enrichment(engine, world, tmp_path):
    gp = next(r for r in world.rows if r.get("gpani_source_geocode"))
    depth = int(gp["gpani_tubewell_depth_raw"])
    ftype = "Deep" if "Deep" in gp["facility_type"] else "Shallow"
    general = ["SYNBL-NEW-1", "SYN-PROJ", "Owner", gp["gpani_source_geocode"], str(gp["longitude"]), str(gp["latitude"]),
               gp["division"].title(), gp["district"].title(), gp["upazila"].title(), gp["union_name"].title(), "",
               "2024-01-01", "2024-01-02", "Rotary", f"{depth + 3} m", f"{depth} m", ftype]
    src = tmp_path / "b.xlsx"
    write_borelog_workbook(str(src), {"General Information": [general]})
    plan = preview(engine, str(src))
    rec = plan.records[0]
    assert rec.match.level == "L2_VERIFIED_CROSS_SOURCE" and rec.classification == ChangeClass.NEW_OBSERVATION
    apply_all(engine, plan)
    _h, rows = table(current_path(engine))
    row = next(r for r in rows if r.get("candidate_id") == gp["candidate_id"])
    assert row["borelog_id"] == "SYNBL-NEW-1" and row["record_sources"] == "GPANI;BORELOG"
    assert row["borelog_match_status"] == "MERGED_GPANI_UNIQUE_TW_ID_ADMIN_GPS_TYPE_DEPTH"
    assert len(rows) == len(world.rows)


def test_audit_files_are_complete(engine, world, tmp_path):
    plan = preview(engine, write_survey(tmp_path / "a.xlsx", [aqua_row(1, DRY, aqua_rows(world)[0])]))
    result = apply_all(engine, plan)
    for stage_dir in (plan.audit_dir, result["audit_dir"]):
        names = set(os.listdir(stage_dir))
        assert {"sync_summary.json", "record_changes.csv", "matched_resources.csv", "new_facilities.csv",
                "skipped_duplicates.csv", "validation_errors.csv", "conflicts.csv", "field_change_history.csv",
                "report.html"} <= names
    summary = json.load(open(os.path.join(result["audit_dir"], "sync_summary.json")))
    assert summary["version"] == 1 and summary["validation"]["ok"]
    version = engine.store.version(1)
    for key in ("created_at", "previous_version", "sources", "schema_version", "engine_version", "counts", "validation"):
        assert key in version
    assert version["sources"][0]["sha256"]
