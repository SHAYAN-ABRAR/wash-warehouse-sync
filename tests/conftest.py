"""Shared fixtures: small synthetic worlds, an initialized engine and source builders."""

from __future__ import annotations

import copy
import os
import random
import re
import zipfile
from typing import Any

import pytest
from wash_sync.config import Settings
from wash_sync.engine import Engine
from wash_sync.planner import PreviewInput
from wash_sync.synthetic import (
    AQUA_SURVEY_HEADERS,
    build_world,
    make_aqua,
    random_geo,
    write_table,
    write_world_warehouse,
)
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


def edited_warehouse(world, path: os.PathLike[str] | str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """The warehouse as edited outside this tool: one AquaWatch facility added and one value corrected."""
    edited = copy.deepcopy(world)
    rng = random.Random(99)
    added = make_aqua(rng, random_geo(rng, edited.admin_tree), 9001, ["2025"])
    corrected = next(r for r in edited.rows if isinstance(r.get("aqua_2025_arsenic_raw"), float))
    corrected["aqua_2025_arsenic_raw"] = 0.077
    edited.rows.append(added)
    write_world_warehouse(edited, str(path))
    return str(path), added, corrected


def to_shared_strings(src: str, dst: str) -> None:
    """Re-store a workbook the way Excel saves text: every inline string becomes a shared string."""
    with zipfile.ZipFile(src) as zin:
        members = {name: zin.read(name) for name in zin.namelist()}
    sheet = next(n for n in members if n.startswith("xl/worksheets/sheet"))
    strings: dict[bytes, int] = {}

    def shared(m: re.Match[bytes]) -> bytes:
        idx = strings.setdefault(m.group(2), len(strings))
        return b"<c" + m.group(1).replace(b' t="inlineStr"', b' t="s"') + b"><v>%d</v></c>" % idx

    members[sheet] = re.sub(rb'<c\b([^>]*\bt="inlineStr"[^>]*)><is><t(?:\s[^>]*)?>(.*?)</t></is></c>', shared,
                            members[sheet], flags=re.S)
    members["xl/sharedStrings.xml"] = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<sst xmlns="http://schemas.openxmlformats.org/'
        b'spreadsheetml/2006/main" count="%d" uniqueCount="%d">' % (len(strings), len(strings))
        + b"".join(b'<si><t xml:space="preserve">' + text + b"</t></si>" for text in strings) + b"</sst>")
    members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
        b"</Types>", b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
                     b'officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
    members["xl/_rels/workbook.xml.rels"] = members["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>", b'<Relationship Id="rIdSharedStrings" Type="http://schemas.openxmlformats.org/'
                             b'officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in members.items():
            zout.writestr(name, data)
