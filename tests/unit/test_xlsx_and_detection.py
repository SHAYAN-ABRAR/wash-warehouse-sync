"""XLSX streaming reader/writer, header detection, fingerprints and lithology."""

import json
import zipfile

import openpyxl
import pytest
from wash_sync.adapters.base import AdapterContext, default_resolver, detect_source, read_records
from wash_sync.adapters.borelog import comparable_layers, lithology_profile
from wash_sync.changes import layers_appended, values_equal
from wash_sync.records import compute_fingerprint
from wash_sync.synthetic import AQUA_SURVEY_HEADERS, BORE_TABS, write_borelog_workbook, write_table
from wash_sync.xlsx.create import SheetSpec, write_workbook
from wash_sync.xlsx.reader import SheetStream, iter_values, parse_cells, read_workbook_info
from wash_sync.xlsx.verify import verify_workbook
from wash_sync.xlsx.writer import (
    CellWrite,
    NewRow,
    RewriteSpec,
    UnsafeWorkbookError,
    escape_text,
    rewrite_workbook,
)


def _wb(path, headers, rows, **kw):
    write_workbook(str(path), [SheetSpec("FLAT_WASH_WAREHOUSE", headers, rows, autofilter=True, freeze=(1, 1), **kw)])


def test_roundtrip_special_characters(tmp_path):
    tricky = ["a & b < c > d", "  padded  ", "tab\there", "ctl\x01char", "_x0041_ literal", "বাংলা", 'quote "x"']
    path = tmp_path / "t.xlsx"
    _wb(path, ["id", "text"], [[i, t] for i, t in enumerate(tricky)])
    values = {r: v for r, v in iter_values(str(path))}
    assert [values[i + 2][2] for i in range(len(tricky))] == tricky
    wb = openpyxl.load_workbook(path, read_only=True)
    via_openpyxl = [r[1] for r in wb.active.iter_rows(min_row=2, values_only=True)]
    # openpyxl does not decode OOXML _xHHHH_ control-character escapes (Excel does)
    plain = [i for i, t in enumerate(tricky) if "\x01" not in t and "_x" not in t]
    assert [via_openpyxl[i] for i in plain] == [tricky[i] for i in plain]


def test_escape_text_controls():
    assert escape_text("a\x01b") == "a_x0001_b"
    assert escape_text("_x0041_") == "_x005F_x0041_"


def test_parse_cells_fast_and_generic_paths():
    fast = parse_cells(b'<c r="A2" s="6" t="inlineStr"><is><t>x</t></is></c><c r="B2"><v>1.5</v></c><c r="C2" s="6"/>')
    assert [(c.col, c.value) for c in fast] == [(1, "x"), (2, 1.5), (3, None)]
    generic = parse_cells(b'<c t="inlineStr" r="D2"><is><r><t>ri</t></r><r><t>ch</t></r></is></c>')
    assert generic[0].col == 4 and generic[0].value == "rich"


def test_rewrite_inserts_updates_and_columns(tmp_path):
    base = tmp_path / "base.xlsx"
    headers = ["candidate_id", "division", "aqua_2026_wet_source_row", "borelog_id"]
    _wb(base, headers, [["A", "DHAKA", 1, "B1"], ["B", "DHAKA", None, None], ["C", "SYLHET", 3, "B3"]])
    final = headers[:3] + ["aqua_2027_dry_arsenic_raw"] + headers[3:]
    spec = RewriteSpec(base_headers=headers, final_headers=final, header_styles={"aqua_2027_dry_arsenic_raw": None},
                       updates={3: {"aqua_2027_dry_arsenic_raw": CellWrite(0.01, "number")}},
                       inserts={3: [NewRow("k", {"candidate_id": CellWrite("NEW", "string"),
                                                 "division": CellWrite("DHAKA", "string")})]})
    out = tmp_path / "out.xlsx"
    res = rewrite_workbook(str(base), str(out), spec)
    assert res.inserted_rows == 1 and res.final_ref == "A1:E5"
    ws = openpyxl.load_workbook(out).active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    assert rows[0] == final
    assert rows[2] == ["B", "DHAKA", None, 0.01, None]
    assert rows[3] == ["NEW", "DHAKA", None, None, None]
    assert rows[4] == ["C", "SYLHET", 3, None, "B3"]  # shifted row and shifted column preserved
    assert ws.auto_filter.ref == "A1:E5"
    report = verify_workbook(str(out), expected_headers=final, expected_rows=4)
    assert report.ok, [c for c in report.checks if c.status == "FAIL"]
    workbook_xml = zipfile.ZipFile(out).read("xl/workbook.xml").decode()
    assert "$A$1:$E$5" in workbook_xml


def test_structural_edit_refused_with_formulas(tmp_path):
    base = tmp_path / "f.xlsx"
    _wb(base, ["a", "b"], [[1, 2]])
    with zipfile.ZipFile(base) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
        b'<c r="B2"><v>2</v></c>', b'<c r="B2"><f>A2*2</f><v>2</v></c>')
    with zipfile.ZipFile(base, "w") as zf:
        for n, data in parts.items():
            zf.writestr(n, data)
    spec = RewriteSpec(base_headers=["a", "b"], final_headers=["a", "b"], inserts={1: [NewRow("k", {"a": CellWrite(9, "number")})]})
    with pytest.raises(UnsafeWorkbookError, match="formulas"):
        rewrite_workbook(str(base), str(tmp_path / "o.xlsx"), spec)


def test_excel_row_limit_fails_visibly(tmp_path):
    base = tmp_path / "big.xlsx"
    _wb(base, ["a"], [[1]])
    with zipfile.ZipFile(base) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(b'ref="A1:A2"', b'ref="A1:A1048576"')
    with zipfile.ZipFile(base, "w") as zf:
        for n, data in parts.items():
            zf.writestr(n, data)
    spec = RewriteSpec(base_headers=["a"], final_headers=["a"], inserts={2: [NewRow("k", {"a": CellWrite(2, "number")})]})
    with pytest.raises(UnsafeWorkbookError, match="single sheet"):
        rewrite_workbook(str(base), str(tmp_path / "o.xlsx"), spec)


def test_overlong_cell_is_refused_not_truncated(tmp_path):
    base = tmp_path / "b.xlsx"
    _wb(base, ["a"], [["x"]])
    spec = RewriteSpec(base_headers=["a"], final_headers=["a"], updates={2: {"a": CellWrite("y" * 40000, "string")}})
    with pytest.raises(UnsafeWorkbookError, match="not truncated"):
        rewrite_workbook(str(base), str(tmp_path / "o.xlsx"), spec)


def test_sheet_stream_prologue_and_epilogue(tmp_path):
    path = tmp_path / "s.xlsx"
    _wb(path, ["a"], [[1], [2]])
    info = read_workbook_info(str(path))
    stream = SheetStream(str(path), info.sheets[0].member)
    nums = [r.num for r in stream.rows()]
    assert nums == [1, 2, 3]
    assert b"<dimension" in stream.prologue and b"autoFilter" in stream.epilogue


# ----------------------------------------------------------------------------- detection


def _aqua_row(i, rid, period="2027 (Dry Season)"):
    return [i, period, rid, "23.7", "90.4", "Deep", "120", "Dhaka", "Dhaka", "Savar", "Ashulia", "6.1", "0.01", "0.4", "30"]


def test_detection_by_headers_any_order_and_case(tmp_path):
    headers = list(AQUA_SURVEY_HEADERS)
    reordered = headers[:11] + ["Arsenic (AS)", "Water Table (m)", "Iron (Fe)", "Chloride (Cl)"]
    rows = [[r[i] for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 11, 13, 14)] for r in [_aqua_row(1, "SYN-1")]]
    path = tmp_path / "survey.csv"
    write_table(str(path), reordered, rows)
    det = detect_source(str(path))
    assert det.source == "AQUAWATCH" and det.format == "aquawatch_survey"
    recs = list(read_records(str(path), AdapterContext(default_resolver(), "survey.csv", str(path)), det))
    assert recs[0].fields["aqua_2027_dry_arsenic_raw"].value == "0.01"
    assert recs[0].fields["aqua_2027_dry_water_table_m"].value == 6.1


def test_missing_required_columns_not_detected(tmp_path):
    path = tmp_path / "x.csv"
    write_table(str(path), ["SL", "Division", "Arsenic (As)"], [[1, "Dhaka", "0.1"]])
    det = detect_source(str(path))
    assert det.source is None or det.missing_required


def test_personal_data_columns_are_ignored(tmp_path):
    from wash_sync.synthetic import WPMS_HEADERS

    path = tmp_path / "wpms.xlsx"
    write_table(str(path), WPMS_HEADERS, [[1, "P", "Proj", "ঢাকা", "Dhaka", "Savar", "U", "V", "98000000000000000000001",
                                           "Private Person", "01700000000", "Deep Tube Well Other Pumps", "120",
                                           "Arsenic: 0.01 mg/L", "2024-01-01", "", "চলমান", "23.7", "90.4", ""]])
    det = detect_source(str(path))
    assert det.source == "WPMS" and set(det.personal_data_columns) >= {"Caretaker Name", "Mobile Number"}
    rec = next(read_records(str(path), AdapterContext(default_resolver(), "wpms.xlsx", str(path)), det))
    dumped = json.dumps({k: v.value for k, v in rec.fields.items()}, ensure_ascii=False) + json.dumps(rec.preview, ensure_ascii=False)
    assert "Private Person" not in dumped and "01700000000" not in dumped


def test_borelog_workbook_detection_and_aggregation(tmp_path):
    path = tmp_path / "bore.xlsx"
    write_borelog_workbook(str(path), {
        "General Information": [["SYNBL1", "P", "Owner", "97000000000000000000001", "90.4", "23.7", "Dhaka", "Dhaka",
                                 "Savar", "Ashulia", "", "2024-01-01", "2024-01-02", "Rotary", "750 ft", "৬৬০ ft", "Deep"]],
        "Lithology & Well Description": [["SYNBL1", "", "", "0 m", "6 m", "Grey", "C1", "Clay", "S1", ""],
                                         ["SYNBL1", "", "", "6 m", "12 m", "Grey", "C1", "Sand", "S2", ""]],
        "Surface Water Information": [["SYNBL1", "River A", "Little", "Good"], ["SYNBL1", "River B", "Dry up", "Good"]],
    })
    det = detect_source(str(path))
    assert det.source == "BORELOG"
    rec = next(read_records(str(path), AdapterContext(default_resolver(), "bore.xlsx", str(path)), det))
    assert rec.coords.status == "REVERSED_CORRECTED" and "BORELOG_SWAPPED_SOURCE_COORDINATES" in rec.flags
    assert rec.fields["borelog_drilling_depth_m"].value == pytest.approx(228.6)
    assert rec.fields["borelog_well_depth_m"].value == pytest.approx(201.168)
    assert rec.fields["borelog_lithology_layer_count"].value == 2
    assert len(json.loads(rec.fields["borelog_surface_observations_raw"].value)) == 2
    assert "BORELOG_MULTIPLE_SURFACE_OBSERVATIONS" in rec.flags
    assert set(BORE_TABS) >= {"General Information", "Lithology & Well Description"}


# ----------------------------------------------------------------------------- fingerprints & lithology


def test_fingerprint_ignores_provenance_and_formatting(tmp_path):
    rows = [_aqua_row(1, "SYN-1"), _aqua_row(2, "SYN-2")]
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    write_table(str(a), AQUA_SURVEY_HEADERS, rows)
    shuffled = [list(r) for r in reversed(rows)]
    shuffled[0][11] = "6.10"  # same number, different formatting
    write_table(str(b), AQUA_SURVEY_HEADERS, shuffled)
    ctx = AdapterContext(default_resolver(), "x", "x")
    fa = {r.observation_key: compute_fingerprint(r) for r in read_records(str(a), ctx)}
    fb = {r.observation_key: compute_fingerprint(r) for r in read_records(str(b), ctx)}
    assert fa == fb  # row order and source_row changed, content did not


def test_lithology_interval_checks_and_comparison():
    layers = [{"row": 5, "from": "0 m", "to": "3 m"}, {"row": 6, "from": "4 m", "to": "4 m"}, {"row": 7, "from": "", "to": ""}]
    _l, flags = lithology_profile(layers)
    assert "INTERVAL_GAP_BEFORE_ROW_6" in flags and "NON_POSITIVE_THICKNESS_ROW_6" in flags
    assert "MISSING_INTERVAL_ENDPOINT_ROW_7" in flags
    old = json.dumps([{"row": 10, "from": "0 m", "to": "3 m", "lithology": "Clay"}])
    moved = json.dumps([{"row": 99, "from": "0 m", "to": "3 m", "lithology": "Clay"}])
    more = json.dumps([{"row": 99, "from": "0 m", "to": "3 m", "lithology": "Clay"},
                       {"row": 100, "from": "3 m", "to": "6 m", "lithology": "Sand"}])
    assert values_equal("borelog_lithology_profile_raw", old, moved)  # source rows are provenance only
    assert layers_appended(old, more) and not layers_appended(more, old)
    assert comparable_layers("not json") == []
