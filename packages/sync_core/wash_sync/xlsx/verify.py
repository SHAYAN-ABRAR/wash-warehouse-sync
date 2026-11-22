"""Workbook integrity verification.

``verify_workbook`` validates any warehouse file. ``verify_against_base`` proves
that a newly written version preserved every row and cell the plan did not touch,
by streaming the base and the output side by side.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from xml.parsers import expat

from ..schema import EXCEL_MAX_CELL_CHARS, EXCEL_MAX_COLS, EXCEL_MAX_ROWS, col_index, col_letter
from .reader import (
    AUTOFILTER_REF_RE,
    CELL_REF_STRIP_RE_SAFE,
    CELL_ROWNUM_RE,
    DIMENSION_REF_RE,
    SheetStream,
    load_shared_strings,
    parse_cells,
    read_workbook_info,
)
from .writer import STRUCTURAL_BLOCKERS

WORKSHEET_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"


@dataclass
class Check:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str = ""


@dataclass
class VerifyReport:
    path: str
    checks: list[Check] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        status = "PASS" if ok else ("WARN" if ok is None else "FAIL")
        self.checks.append(Check(name, status, detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "checks": [c.__dict__ for c in self.checks],
            "stats": self.stats,
        }


def _expat_wellformed(zf: zipfile.ZipFile, member: str) -> str | None:
    parser = expat.ParserCreate()
    try:
        with zf.open(member) as fh:
            while True:
                chunk = fh.read(4 * 1024 * 1024)
                if not chunk:
                    break
                parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except expat.ExpatError as exc:
        return f"{member}: {exc}"
    return None


def verify_workbook(
    path: str,
    expected_headers: list[str] | None = None,
    expected_rows: int | None = None,
    progress: Callable[[float], None] | None = None,
    deep: bool = True,
    external: bool = False,
) -> VerifyReport:
    """Integrity report for a single-sheet warehouse workbook.

    ``external`` is for workbooks produced outside this engine (the registered baseline or a
    warehouse edited in Excel): a stale declared dimension or filter range is then a warning,
    because it does not affect the data and the next synchronization writes both from the
    actual data. Workbooks written by this engine must match exactly.
    """
    report = VerifyReport(path=path)
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        report.add("zip_container", False, f"Not a valid ZIP/XLSX container: {exc}")
        return report
    with zf:
        bad = zf.testzip() if deep else None
        report.add("zip_crc", bad is None, "all members pass CRC" if bad is None else f"corrupt member: {bad}")
        info = read_workbook_info(path)
        report.add("single_worksheet", len(info.sheets) == 1,
                   f"{len(info.sheets)} worksheet(s): {[s.name for s in info.sheets]}")
        ws_parts = [p for p, ct in info.content_type_overrides.items() if ct == WORKSHEET_CT]
        report.add("content_types_single_sheet", len(ws_parts) == 1, f"worksheet parts: {ws_parts}")
        for member in info.members:
            if member.endswith(".xml") or member.endswith(".rels"):
                if member == info.sheets[0].member and not deep:
                    continue
                err = _expat_wellformed(zf, member)
                if err:
                    report.add("xml_well_formed", False, err)
                    return report
        report.add("xml_well_formed", True, f"{len(info.members)} parts parsed")
        # Text saved by Excel lives in the shared-string table; header and length checks need it.
        shared = load_shared_strings(zf, info.shared_strings_member)
    sheet = info.sheets[0]
    stream = SheetStream(path, sheet.member, progress)
    max_col = 0
    last_row = 0
    n_rows = 0
    ordering_errors: list[str] = []
    long_cells = 0
    header: list[str] = []
    formula_rows = 0
    for row in stream.rows():
        n_rows += 1
        if row.inner and b"<f" in row.inner:
            formula_rows += 1
        if row.num <= last_row:
            ordering_errors.append(f"row {row.num} after {last_row}")
        last_row = row.num
        prev_col = 0
        inner = row.inner or b""
        row_bytes = str(row.num).encode()
        if any(n != row_bytes for n in CELL_ROWNUM_RE.findall(inner)) and len(ordering_errors) < 20:
            ordering_errors.append(f"row {row.num} contains a cell reference to another row")
        for cell in parse_cells(inner, shared):
            if cell.col <= prev_col and len(ordering_errors) < 20:
                ordering_errors.append(f"row {row.num}: columns out of order at {cell.letters.decode()}")
            prev_col = cell.col
            max_col = max(max_col, cell.col)
            if isinstance(cell.value, str) and len(cell.value) > EXCEL_MAX_CELL_CHARS:
                long_cells += 1
            if row.num == 1:
                header.append(str(cell.value))
    report.stats.update({"rows_including_header": n_rows, "last_row": last_row, "max_column": max_col,
                         "data_rows": max(0, n_rows - 1)})
    report.add("row_and_cell_order", not ordering_errors, "; ".join(ordering_errors[:5]) or "strictly increasing")
    report.add("excel_limits", last_row <= EXCEL_MAX_ROWS and max_col <= EXCEL_MAX_COLS,
               f"{last_row:,} rows x {max_col} columns")
    report.add("cell_length_limit", long_cells == 0, f"{long_cells} cells exceed {EXCEL_MAX_CELL_CHARS} chars")
    dim = DIMENSION_REF_RE.search(stream.prologue)
    expected_ref = f"A1:{col_letter(max(1, max_col))}{last_row}"
    refreshed = " (rewritten from the data by the next synchronization)" if external else ""

    def layout(match: bool) -> bool | None:
        return True if match else (None if external else False)

    dim_ok = bool(dim) and dim.group(2).decode() == expected_ref
    report.add("dimension", layout(dim_ok),
               f"declared {dim.group(2).decode() if dim else None}, actual {expected_ref}" + ("" if dim_ok else refreshed))
    af = AUTOFILTER_REF_RE.search(stream.epilogue)
    if af:
        af_ok = af.group(2).decode() == expected_ref
        report.add("autofilter_range", layout(af_ok),
                   f"autoFilter {af.group(2).decode()} vs data {expected_ref}" + ("" if af_ok else refreshed))
        names = [t for (n, _l, t) in info.defined_names if n == "_xlnm._FilterDatabase"]
        if names:
            target = names[0].split("!")[-1].replace("$", "")
            report.add("filter_defined_name", True if target == expected_ref else None,
                       f"_FilterDatabase {names[0]} (sheet autoFilter {af.group(2).decode()})")
    if external:
        blockers = [f"formulas in {formula_rows:,} rows"] if formula_rows else []
        blockers += [label for token, label in STRUCTURAL_BLOCKERS if token in stream.epilogue]
        report.add("sync_compatibility", True if not blockers else None,
                   "no formulas, merged cells, tables or conditional formats" if not blockers else
                   "Contains " + ", ".join(blockers) + ". Syncs that add rows or columns will be refused until these "
                   "are removed (for example, paste values over formulas) and the file is loaded again.")
    frozen = re.search(rb'<pane [^>]*state="frozen"', stream.prologue)
    report.add("frozen_header", True if frozen else None, "frozen panes present" if frozen else "no frozen panes")
    report.stats["header"] = header
    if expected_headers is not None:
        report.add("header_row", header == expected_headers,
                   "matches plan" if header == expected_headers else f"{len(header)} headers vs {len(expected_headers)} expected")
    if expected_rows is not None:
        report.add("row_count", n_rows - 1 == expected_rows, f"{n_rows - 1:,} data rows (expected {expected_rows:,})")
    report.add("independent_reader", *_openpyxl_probe(path, header))
    return report


def _openpyxl_probe(path: str, header: list[str]) -> tuple[bool | None, str]:
    """Bounded cross-check with openpyxl (an independent XLSX implementation)."""
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return None, "openpyxl not installed; skipped"
    try:
        fh = open(path, "rb")  # file object: no dependence on the file extension
        wb = openpyxl.load_workbook(fh, read_only=True)
        try:
            if len(wb.sheetnames) != 1:
                return False, f"openpyxl sees {len(wb.sheetnames)} sheets"
            ws = wb[wb.sheetnames[0]]
            rows = ws.iter_rows(min_row=1, max_row=50, values_only=True)
            first = next(rows, None)
            names = [str(v) for v in (first or []) if v is not None]
            if names != header:
                return False, "openpyxl header differs from streaming reader"
            sampled = sum(1 for _ in rows)
            return True, f"openpyxl opened the workbook and read header + {sampled} rows"
        finally:
            wb.close()
            fh.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"openpyxl failed to read the workbook: {exc}"


# ---------------------------------------------------------------------------
# Preservation proof: base vs. new version
# ---------------------------------------------------------------------------


@dataclass
class PreservationReport:
    compared_rows: int = 0
    patched_rows_checked: int = 0
    inserted_rows_skipped: int = 0
    mismatches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _norm_row(inner: bytes) -> bytes:
    return CELL_REF_STRIP_RE_SAFE.sub(rb'\g<1>"', inner)


def verify_against_base(
    base_path: str,
    out_path: str,
    base_headers: list[str],
    final_headers: list[str],
    inserted_rows: set[int],
    expected_updates: dict[int, dict[str, Any]],
    progress: Callable[[float], None] | None = None,
) -> PreservationReport:
    """Stream base and output together and compare every untouched row and cell.

    ``expected_updates`` maps *output* row numbers to ``{column: expected value}``.
    """
    report = PreservationReport()
    final_pos = {name: i + 1 for i, name in enumerate(final_headers)}
    col_map = {i + 1: final_pos[name] for i, name in enumerate(base_headers)}
    first_shifted = min((k for k, v in col_map.items() if k != v), default=10**9)
    base_info = read_workbook_info(base_path)
    out_info = read_workbook_info(out_path)
    base_rows = SheetStream(base_path, base_info.sheets[0].member, progress).rows()
    out_rows = SheetStream(out_path, out_info.sheets[0].member).rows()
    next(base_rows, None)
    next(out_rows, None)  # headers are verified separately
    final_names = {v: k for k, v in final_pos.items()}

    def note(msg: str) -> None:
        if len(report.mismatches) < 50:
            report.mismatches.append(msg)
        else:
            report.mismatches[-1] = "... further mismatches truncated"

    for base in base_rows:
        out = next(out_rows, None)
        while out is not None and out.num in inserted_rows:
            report.inserted_rows_skipped += 1
            out = next(out_rows, None)
        if out is None:
            note(f"output ended before base row {base.num}")
            break
        report.compared_rows += 1
        expected = expected_updates.get(out.num)
        shifted = base.last_col() >= first_shifted
        if expected is None and not shifted:
            if _norm_row(base.inner or b"") != _norm_row(out.inner or b""):
                note(f"base row {base.num} (output row {out.num}) changed although the plan did not touch it")
            continue
        report.patched_rows_checked += 1
        b_cells = {col_map[c.col]: c for c in parse_cells(base.inner or b"")}
        o_cells = {c.col: c for c in parse_cells(out.inner or b"")}
        expected = expected or {}
        for idx in set(b_cells) | set(o_cells):
            name = final_names.get(idx, f"col{idx}")
            if name in expected:
                got = o_cells.get(idx)
                want = expected[name]
                if got is None or not _same_value(got.value, want):
                    note(f"output row {out.num} {name}: expected {want!r}, found {got.value if got else None!r}")
                continue
            b_cell, o_cell = b_cells.get(idx), o_cells.get(idx)
            if b_cell is None or o_cell is None:
                note(f"output row {out.num} {name}: cell {'added' if b_cell is None else 'removed'} unexpectedly")
            elif (b_cell.value, b_cell.type, b_cell.style) != (o_cell.value, o_cell.type, o_cell.style):
                note(f"output row {out.num} {name}: {b_cell.value!r} became {o_cell.value!r}")
    leftover = next(out_rows, None)
    while leftover is not None:
        if leftover.num not in inserted_rows:
            note(f"unexpected extra output row {leftover.num}")
            break
        report.inserted_rows_skipped += 1
        leftover = next(out_rows, None)
    return report


def _same_value(got: Any, want: Any) -> bool:
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(float(got) - float(want)) <= 1e-9 * max(1.0, abs(float(want)))
    return str(got) == str(want)


__all__ = ["verify_workbook", "verify_against_base", "VerifyReport", "PreservationReport", "col_index"]
