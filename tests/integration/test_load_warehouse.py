"""Loading the current warehouse file: baseline, newer edited versions, safety checks and index readiness."""

from __future__ import annotations

import os
import shutil
import threading

import openpyxl
import pytest
from wash_sync.config import Settings
from wash_sync.engine import Engine, EngineError, IndexNotReady
from wash_sync.index import WarehouseIndex
from wash_sync.synthetic import write_world_warehouse

from tests.conftest import (
    aqua_row,
    aqua_rows,
    current_path,
    edited_warehouse,
    preview,
    table,
    to_shared_strings,
    write_survey,
)

DRY_2027 = "2027 (Dry Season)"


def test_first_load_registers_the_baseline(tmp_path, world):
    wh = tmp_path / "warehouse.xlsx"
    write_world_warehouse(world, str(wh))
    state = str(tmp_path / "state")
    eng = Engine(Settings(state_dir=state), state)
    record = eng.load_warehouse(str(wh), actor="pytest")
    assert record["version"] == 0 and record["kind"] == "BASELINE" and record["imported_from"] == "warehouse.xlsx"
    idx = eng.index(build=False)  # prepared before the version became current
    try:
        assert idx.n_rows == len(world.rows)
    finally:
        idx.close()
    assert any(e["event"] == "INIT" for e in eng.history()["events"])


def test_loading_the_current_file_changes_nothing(engine):
    before = engine.store.versions()
    record = engine.load_warehouse(current_path(engine), actor="pytest")
    assert record["unchanged"] and record["version"] == 0
    assert engine.store.versions() == before


def test_edited_warehouse_becomes_the_next_version_and_syncs_against_it(engine, world, tmp_path):
    path, added, _ = edited_warehouse(world, tmp_path / "edited.xlsx")
    record = engine.load_warehouse(path, actor="pytest", reason="QA corrections made in Excel")
    assert record["version"] == 1 and record["kind"] == "LOADED" and record["previous_version"] == 0
    assert record["rows"] == len(world.rows) + 1 and record["reason"] == "QA corrections made in Excel"
    assert engine.store.current_version()["version"] == 1
    idx = engine.index(build=False)  # the explorer can read the new version at once
    try:
        assert idx.n_rows == len(world.rows) + 1
        assert len(idx.rows_where("aqua_gwc_id", added["aqua_gwc_id"])) == 1
    finally:
        idx.close()
    event = next(e for e in engine.history()["events"] if e["event"] == "LOAD_WAREHOUSE")
    assert event["version"] == 1 and event["reason"] == "QA corrections made in Excel" and event["imported_from"] == "edited.xlsx"

    # The next sync compares against the loaded file: the facility added in Excel is not added twice.
    plan = preview(engine, write_survey(tmp_path / "s.xlsx", [aqua_row(1, DRY_2027, added)]))
    by_class = plan.summary["by_classification"]
    assert by_class["NEW_OBSERVATION"] == 1 and by_class["NEW_FACILITY"] == 0

    engine.rollback(0, "pytest", "back to the registered baseline")  # earlier versions stay available
    assert engine.store.current_version()["version"] == 0


def test_plans_prepared_before_a_load_are_refused(engine, world, tmp_path):
    plan = preview(engine, write_survey(tmp_path / "s.xlsx", [aqua_row(1, DRY_2027, aqua_rows(world)[0])]))
    engine.load_warehouse(edited_warehouse(world, tmp_path / "edited.xlsx")[0], actor="pytest")
    with pytest.raises(EngineError, match="prepared for version 0"):
        engine.apply(plan.plan_id, "pytest", authorize=True)


def test_a_source_export_is_not_accepted_as_the_warehouse(engine, world, tmp_path):
    src = write_survey(tmp_path / "survey.xlsx", [aqua_row(1, DRY_2027, aqua_rows(world)[0])])
    with pytest.raises(EngineError, match="not the WASH warehouse.*AquaWatch source export.*Files to sync"):
        engine.load_warehouse(src, actor="pytest")
    assert len(engine.store.versions()) == 1


def test_a_copy_of_an_earlier_version_is_refused(engine, world, tmp_path):
    original = tmp_path / "copy_of_v000.xlsx"
    shutil.copyfile(current_path(engine), original)
    engine.load_warehouse(edited_warehouse(world, tmp_path / "edited.xlsx")[0], actor="pytest")
    with pytest.raises(EngineError, match="identical to v000.*Make current"):
        engine.load_warehouse(str(original), actor="pytest")
    assert engine.store.current_version()["version"] == 1


def test_unreadable_file_is_refused(engine, tmp_path):
    bad = tmp_path / "not_really.xlsx"
    bad.write_text("hello")
    with pytest.raises(EngineError, match="could not be read"):
        engine.load_warehouse(str(bad), actor="pytest")


def test_registering_twice_points_to_loading(engine):
    with pytest.raises(EngineError, match="load-warehouse"):
        engine.init(current_path(engine), actor="pytest")


def test_concurrent_requests_build_an_index_only_once(engine, monkeypatch):
    shutil.rmtree(engine.store.p("index"))
    builds: list[int] = []
    original = WarehouseIndex.build.__func__

    def counting(cls, *args, **kwargs):  # type: ignore[no-untyped-def]
        builds.append(1)
        return original(cls, *args, **kwargs)

    monkeypatch.setattr(WarehouseIndex, "build", classmethod(counting))
    rows: list[int] = []
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            idx = engine.index()
            rows.append(idx.n_rows)
            idx.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(builds) == 1 and len(rows) == 4 and len(set(rows)) == 1


def test_reading_never_builds_a_missing_index(engine):
    shutil.rmtree(engine.store.p("index"))
    with pytest.raises(IndexNotReady) as info:
        engine.index(build=False)
    assert info.value.version == 0 and info.value.progress is None
    assert not os.path.exists(engine.store.p("index")) or not os.listdir(engine.store.p("index"))


def test_a_warehouse_edited_in_excel_can_be_loaded_and_synced(engine, tmp_path):
    """Download → edit in a spreadsheet program (re-saved, a well typed in at the bottom) → load → sync."""
    wb = openpyxl.load_workbook(current_path(engine))
    ws = wb.active
    headers = [c.value for c in ws[1]]
    template = next(r for r in ws.iter_rows(min_row=2, values_only=True) if r[headers.index("aqua_gwc_id")])
    row = list(template)
    row[headers.index("candidate_id")] = "AQUA2025:SYN-HAND-1"
    row[headers.index("aqua_gwc_id")] = "SYN-HAND-1"
    ws.append(row)  # the filter range is now one row short, as after a hand edit in Excel
    edited = tmp_path / "edited_in_excel.xlsx"
    wb.save(edited)

    record = engine.load_warehouse(str(edited), actor="pytest", reason="well added by hand")
    warnings = {c["name"] for c in record["validation"]["checks"] if c["status"] == "WARN"}
    assert record["kind"] == "LOADED" and record["validation"]["ok"] and "autofilter_range" in warnings
    assert next(c for c in record["validation"]["checks"] if c["name"] == "sync_compatibility")["status"] == "PASS"

    well = dict(zip(headers, row))
    plan = preview(engine, write_survey(tmp_path / "s.xlsx", [aqua_row(1, "2027 (Dry Season)", well)]))
    assert plan.summary["by_classification"]["NEW_OBSERVATION"] == 1 and plan.summary["by_classification"]["NEW_FACILITY"] == 0
    result = engine.apply(plan.plan_id, "pytest", authorize=True)
    assert result["new_version"] == 2  # new period columns were added on top of the hand-edited file
    report = engine.verify()  # the engine's own output is held to the exact rules again
    assert report["ok"] and next(c for c in report["checks"] if c["name"] == "autofilter_range")["status"] == "PASS"
    _, rows = table(current_path(engine))
    hand = next(r for r in rows if r.get("aqua_gwc_id") == "SYN-HAND-1")
    assert hand["aqua_2027_dry_water_table_m"] == 5.5


def test_formulas_in_a_loaded_file_are_flagged_before_any_sync(engine, tmp_path):
    wb = openpyxl.load_workbook(current_path(engine))
    ws = wb.active
    ws.cell(row=2, column=ws.max_column + 1, value="=1+1")  # a helper formula typed next to the data
    edited = tmp_path / "with_formula.xlsx"
    wb.save(edited)
    record = engine.load_warehouse(str(edited), actor="pytest")
    check = next(c for c in record["validation"]["checks"] if c["name"] == "sync_compatibility")
    assert check["status"] == "WARN" and "formulas in 1 rows" in check["detail"] and "paste values" in check["detail"]


def test_a_warehouse_saved_with_shared_strings_like_excel_syncs_correctly(engine, world, tmp_path):
    excel_like = tmp_path / "saved_by_excel.xlsx"
    to_shared_strings(current_path(engine), str(excel_like))
    before_headers, before_rows = table(current_path(engine))
    assert table(str(excel_like)) == (before_headers, before_rows)  # same content, Excel's storage layout
    record = engine.load_warehouse(str(excel_like), actor="pytest", reason="saved by Excel")
    assert record["kind"] == "LOADED" and record["validation"]["ok"]

    well = aqua_rows(world)[0]
    new = aqua_row(99, DRY_2027, None, **{"Ground Water Resource": "SYN-NEW-SS", "Latitude": 25.1, "Longitude": 89.0,
                                          "Division": "Rangpur", "District": "Testdist", "Type of Tubewell": "Deep"})
    plan = preview(engine, write_survey(tmp_path / "s.xlsx", [aqua_row(1, DRY_2027, well), new]))
    by_class = plan.summary["by_classification"]
    assert by_class["NEW_OBSERVATION"] == 1 and by_class["NEW_FACILITY"] == 1
    assert engine.apply(plan.plan_id, "pytest", authorize=True)["new_version"] == 2  # rows inserted, columns added
    assert engine.verify()["ok"]

    _, after_rows = table(current_path(engine))
    after = {r["candidate_id"]: r for r in after_rows}
    changed = {(row["candidate_id"], k) for row in before_rows
               for k, v in row.items() if k != "_row" and after[row["candidate_id"]].get(k) != v}
    # Every shared-string value still resolves to the same text after rows and columns shifted; the only
    # change is the planned provenance update on the well that received the new observation.
    assert changed == {(well["candidate_id"], "record_sources")}
    assert after[well["candidate_id"]]["record_sources"].endswith(";AQUA_2027_DRY")
    assert after[well["candidate_id"]]["aqua_2027_dry_water_table_m"] == 5.5
    assert any(r.get("aqua_gwc_id") == "SYN-NEW-SS" for r in after_rows)
