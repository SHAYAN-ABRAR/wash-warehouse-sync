"""Large synthetic workload test (opt-in): ``pytest -m scale -s``.

Generates a ~600,000 × 196 synthetic warehouse, registers it, previews a source with
thousands of mixed records (new, changed, unchanged, conflicting, invalid), applies
it, verifies the result, re-runs the same source to prove idempotency at scale and
reports elapsed time and peak memory per phase.

Environment:
    WASH_SCALE_ROWS   number of warehouse rows (default 600000)
    WASH_SCALE_DIR    working directory (use a disk-backed path; generated files are large)
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

import pytest
from wash_sync.config import Settings
from wash_sync.engine import Engine
from wash_sync.planner import PreviewInput
from wash_sync.synthetic import AQUA_SURVEY_HEADERS, write_synthetic_warehouse, write_table

pytestmark = pytest.mark.scale


def _rss_mb() -> float:
    try:
        import resource  # noqa: PLC0415  (POSIX only)
    except ImportError:
        return float("nan")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024  # bytes on macOS, KiB on Linux


def _survey(serial: int, period: str, w: dict, **values) -> list:
    base = {"SL": serial, "Survey Year": period, "Ground Water Resource": w.get("aqua_gwc_id"),
            "Latitude": w.get("latitude"), "Longitude": w.get("longitude"), "Type of Tubewell": w.get("aqua_well_type"),
            "Depth of Tubewell": w.get("aqua_well_depth_m"), "Division": w.get("division"), "District": w.get("district"),
            "Upazila": w.get("upazila"), "Union": w.get("union_name"), "Water Table (m)": "5.25", "Arsenic (As)": "0.012",
            "Iron (Fe)": "0.42", "Chloride (Cl)": "55"}
    base.update(values)
    return [base[h] for h in AQUA_SURVEY_HEADERS]


def test_scale_end_to_end(tmp_path):
    rows = int(os.environ.get("WASH_SCALE_ROWS", "600000"))
    work = os.environ.get("WASH_SCALE_DIR") or str(tmp_path)
    os.makedirs(work, exist_ok=True)
    timings: dict[str, float] = {}
    peaks: dict[str, float] = {}

    # perf_counter is monotonic and pauses while the computer sleeps, so a suspended laptop or a clock
    # adjustment cannot inflate a phase.
    def phase(name: str, started: float) -> None:
        timings[name] = round(time.perf_counter() - started, 1)
        peaks[name] = round(_rss_mb(), 1)
        print(f"[scale] {name}: {timings[name]}s (peak RSS so far {peaks[name]} MB)", flush=True)

    wh = os.path.join(work, "synthetic_scale_warehouse.xlsx")
    t = time.perf_counter()
    info = write_synthetic_warehouse(wh, rows=rows, columns=196, seed=7)
    phase("generate_warehouse", t)

    state = os.path.join(work, "state")
    eng = Engine(Settings(state_dir=state), state)
    t = time.perf_counter()
    baseline = eng.init(wh, actor="scale-test")
    phase("init_verify_and_index", t)
    assert baseline["rows"] == rows and baseline["columns"] == 196

    idx = eng.index()
    wells = []
    for r in idx.conn.execute("SELECT row_num, cells FROM rows WHERE aqua_gwc_id IS NOT NULL ORDER BY row_num"):
        wells.append(idx._decode(r["cells"]))
    idx.close()
    with_wet = [w for w in wells if "aqua_2026_wet_arsenic_raw" in w]
    rng = random.Random(3)
    src_rows: list[list] = []
    n = 0
    for w in wells[:3000]:  # new survey period for existing wells
        n += 1
        src_rows.append(_survey(n, "2027 (Dry Season)", w))
    for w in with_wet[:1000]:  # identical to the warehouse
        n += 1
        src_rows.append(_survey(n, "2026 (Wet Season)", w, **{
            "Arsenic (As)": str(w["aqua_2026_wet_arsenic_raw"]), "Water Table (m)": str(w["aqua_2026_wet_water_table_m"]),
            "Iron (Fe)": str(w["aqua_2026_wet_iron_raw"]), "Chloride (Cl)": str(w["aqua_2026_wet_chloride_raw"])}))
    for w in with_wet[1000:1500]:  # corrected measurements → review
        n += 1
        src_rows.append(_survey(n, "2026 (Wet Season)", w, **{
            "Arsenic (As)": "0.777", "Water Table (m)": str(w["aqua_2026_wet_water_table_m"]),
            "Iron (Fe)": str(w["aqua_2026_wet_iron_raw"]), "Chloride (Cl)": str(w["aqua_2026_wet_chloride_raw"])}))
    for i in range(500):  # new wells (no GPS → cannot be geographic duplicates)
        n += 1
        donor = wells[rng.randrange(len(wells))]
        src_rows.append(_survey(n, "2027 (Dry Season)", donor, **{"Ground Water Resource": f"SYN-SCALE-{i:05d}",
                                                                   "Latitude": None, "Longitude": None}))
    for w in wells[3000:3050]:  # conflicting duplicates within the run
        n += 1
        src_rows.append(_survey(n, "2027 (Dry Season)", w))
        n += 1
        src_rows.append(_survey(n, "2027 (Dry Season)", w, **{"Arsenic (As)": "0.333"}))
    for i in range(50):  # invalid rows
        n += 1
        src_rows.append(_survey(n, "2027 (Monsoon)", wells[i]))
    src = os.path.join(work, "synthetic_scale_source.xlsx")
    write_table(src, AQUA_SURVEY_HEADERS, src_rows, "Survey Data")

    t = time.perf_counter()
    plan = eng.preview([PreviewInput(src)], actor="scale-test")
    phase("preview", t)
    by = {k: v for k, v in plan.summary["by_classification"].items() if v}
    print(f"[scale] classification: {by}", flush=True)
    assert by["NEW_OBSERVATION"] == 3000
    assert by["UNCHANGED"] == 1000
    assert by["UPDATED_MEASUREMENT"] == 500
    assert by["NEW_FACILITY"] == 500
    assert by["CONFLICTING_MEASUREMENT"] == 100
    assert by["INVALID_SOURCE_RECORD"] == 50

    t = time.perf_counter()
    decided = eng.bulk_decide(plan.plan_id, "UPDATED_MEASUREMENT", "ACCEPT_NEW_VALUES", "Scale test: accept corrections", "scale-test")
    eng.approve(plan.plan_id, "scale-test")
    result = eng.apply(plan.plan_id, "scale-test")
    phase("decide_approve_apply_verify", t)
    assert decided == 500
    assert result["new_version"] == 1
    assert result["rows"] == rows + 500
    assert result["counts"]["added_facilities"] == 500
    record = eng.store.version(1)
    assert record["validation"]["ok"]
    assert record["validation"]["preservation"]["compared_rows"] == rows

    t = time.perf_counter()
    rerun = eng.preview([PreviewInput(src)], actor="scale-test")
    phase("idempotent_rerun_preview", t)
    again = {k: v for k, v in rerun.summary["by_classification"].items() if v}
    print(f"[scale] rerun: {again}", flush=True)
    assert again.get("UNCHANGED") == 3000 + 1000 + 500 + 500
    assert "NEW_OBSERVATION" not in again and "NEW_FACILITY" not in again and "UPDATED_MEASUREMENT" not in again
    no_change = eng.apply(rerun.plan_id, "scale-test", authorize=True)
    assert no_change["new_version"] is None  # deferred conflicts stay pending; nothing new is written

    results = {
        "rows": rows, "columns": 196, "source_records": len(src_rows), "timings_seconds": timings,
        "peak_rss_mb": peaks, "classification": by, "rerun_classification": again,
        "workbook_mb": round(os.path.getsize(wh) / 1e6, 1),
        "version_1_mb": round(os.path.getsize(eng.store.version_path(record)) / 1e6, 1),
        "admin_groups": info["admin_groups"],
    }
    os.makedirs(os.path.join(os.path.dirname(__file__), "generated"), exist_ok=True)
    with open(os.path.join(os.path.dirname(__file__), "generated", "scale_results.json"), "w") as fh:
        json.dump(results, fh, indent=2)
    print("[scale] RESULTS " + json.dumps(results), flush=True)
