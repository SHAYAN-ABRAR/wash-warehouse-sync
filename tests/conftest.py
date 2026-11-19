"""Shared fixtures: small synthetic worlds, an initialized engine and source builders."""

from __future__ import annotations

import os
from typing import Any

import pytest

from wash_sync.config import Settings
from wash_sync.engine import Engine
from wash_sync.planner import PreviewInput
from wash_sync.synthetic import AQUA_SURVEY_HEADERS, build_world, write_table, write_world_warehouse
from wash_sync.xlsx.reader import iter_values


@pytest.fixture
def world():
    return build_world(seed=11, gpani=120, wpms=20, aqua=24, borelog=8)


@pytest.fixture
def engine(tmp_path, world):
    wh = tmp_path / "warehouse.xlsx"
    write_world_warehouse(world, str(wh))
    state = str(tmp_path / "state")
    eng = Engine(Settings(state_dir=state), state)
    eng.init(str(wh), actor="pytest")
    return eng


def aqua_row(serial: int, period: str, row: dict[str, Any] | None = None, **values: Any) -> list[Any]:
    """One AquaWatch survey row (columns in AQUA_SURVEY_HEADERS order)."""
    base = {
        "SL": serial, "Survey Year": period, "Ground Water Resource": None, "Latitude": None, "Longitude": None,
        "Type of Tubewell": None, "Depth of Tubewell": None, "Division": None, "District": None, "Upazila": None,
        "Union": None, "Water Table (m)": "5.5", "Arsenic (As)": "0.01", "Iron (Fe)": "0.3", "Chloride (Cl)": "40",
    }
    if row is not None:
        base.update({
            "Ground Water Resource": row["aqua_gwc_id"], "Latitude": row["latitude"], "Longitude": row["longitude"],
            "Type of Tubewell": row["aqua_well_type"], "Depth of Tubewell": row["aqua_well_depth_m"],
            "Division": row["division"].title(), "District": row["district"].title(), "Upazila": row["upazila"].title(),
            "Union": row["union_name"].title(),
        })
    base.update(values)
    return [base[h] for h in AQUA_SURVEY_HEADERS]


def write_survey(path: os.PathLike[str] | str, rows: list[list[Any]]) -> str:
    write_table(str(path), AQUA_SURVEY_HEADERS, rows, "Survey Data")
    return str(path)


def preview(eng: Engine, *paths: str, **kw: Any):
    return eng.preview([PreviewInput(path=str(p), **kw) for p in paths], actor="pytest")


def table(path: str) -> tuple[list[str], list[dict[str, Any]]]:
    rows = list(iter_values(path))
    headers = [rows[0][1][i] for i in sorted(rows[0][1])]
    out = []
    for num, values in rows[1:]:
        out.append({"_row": num, **{headers[c - 1]: v for c, v in values.items()}})
    return headers, out


def current_path(eng: Engine) -> str:
    return eng.store.version_path(eng.store.current_version())


def aqua_rows(world) -> list[dict[str, Any]]:
    return [r for r in world.rows if r.get("aqua_gwc_id")]
