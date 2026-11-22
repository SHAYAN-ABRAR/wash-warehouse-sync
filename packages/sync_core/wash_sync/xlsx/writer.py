"""Streaming workbook rewriter used to publish new warehouse versions.

Guarantees
----------
* The base workbook is opened read-only and is never modified.
* Rows without changes are copied byte-for-byte (only their ``r=`` references are
  renumbered when rows were inserted above them).
* Changed rows keep the exact XML (style, type) of every untouched cell.
* All non-sheet parts (styles, theme, properties) are copied unchanged; only the
  ``_xlnm._FilterDatabase`` defined name is updated to match the new range.
* Structural edits are refused when the sheet contains features whose references
  this writer does not rewrite (formulas, merged cells, conditional formats, ...).
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..numbers import excel_number_text
from ..schema import EXCEL_MAX_CELL_CHARS, EXCEL_MAX_COLS, EXCEL_MAX_ROWS, col_letter
from .reader import AUTOFILTER_REF_RE, DIMENSION_REF_RE, RawRow, SheetStream, parse_cells, read_workbook_info

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_LITERAL_ESC = re.compile(r"_(x[0-9A-Fa-f]{4})_")
CELL_REF_RE = re.compile(rb'(<c r="[A-Z]{1,3})\d+"')
ROW_R_RE = re.compile(rb'\s*\br="\d+"')
ROW_SPANS_RE = re.compile(rb'\s*\bspans="[^"]*"')
CELL_R_ATTR_RE = re.compile(rb'\br="[A-Z]{1,3}\d+"')
#: Sheet features whose references this writer does not rewrite: structural edits (inserted rows
#: or columns) are refused while any of them is present.
STRUCTURAL_BLOCKERS = (
    (b"<mergeCells", "merged cells"), (b"<conditionalFormatting", "conditional formatting"),
    (b"<dataValidations", "data validation"), (b"<hyperlinks", "hyperlinks"), (b"<tableParts", "Excel tables"),
    (b"<ignoredErrors", "ignored-error markers"), (b"<x14:", "Excel 2010 extensions"), (b"<extLst", "extension lists"),
)
UNSUPPORTED_EPILOGUE = tuple(token for token, _label in STRUCTURAL_BLOCKERS)


class OperationCancelled(RuntimeError):
    """Raised when a caller requests cancellation; partial output is discarded."""


class UnsafeWorkbookError(RuntimeError):
    """Raised when an edit could corrupt references the writer does not manage."""


def escape_text(text: str) -> str:
    text = _LITERAL_ESC.sub(r"_x005F_\1_", text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _CONTROL.sub(lambda m: f"_x{ord(m.group(0)):04X}_", text)


@dataclass(slots=True)
class CellWrite:
    """A value to place in a cell. ``kind`` is ``number`` or ``string``."""

    value: Any
    kind: str
    style: str | None = None


def cell_xml(letters: str, row: int, write: CellWrite, keep_style: bytes | None = None) -> bytes:
    style = keep_style.decode() if keep_style is not None else write.style
    s_attr = f' s="{style}"' if style not in (None, "", "-") else ""
    ref = f"{letters}{row}"
    if write.kind == "number":
        value = write.value
        if isinstance(value, str):
            value = float(value)
        return f'<c r="{ref}"{s_attr}><v>{excel_number_text(value)}</v></c>'.encode()
    text = str(write.value)
    if len(text) > EXCEL_MAX_CELL_CHARS:
        raise UnsafeWorkbookError(
            f"Value for cell {ref} has {len(text)} characters; Excel's limit is {EXCEL_MAX_CELL_CHARS}. "
            "The value was not truncated — resolve it in review."
        )
    space = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
    return f'<c r="{ref}"{s_attr} t="inlineStr"><is><t{space}>{escape_text(text)}</t></is></c>'.encode()


@dataclass(slots=True)
class NewRow:
    key: str  # deterministic ordering among rows inserted at the same anchor
    cells: dict[str, CellWrite]


@dataclass
class RewriteSpec:
    """Everything the streaming rewriter needs; produced by the apply planner."""

    base_headers: list[str]
    final_headers: list[str]
    header_styles: dict[str, str | None] = field(default_factory=dict)  # for new columns
    widths: dict[str, float | None] = field(default_factory=dict)  # for new columns
    updates: dict[int, dict[str, CellWrite]] = field(default_factory=dict)  # base row -> col -> write
    inserts: dict[int, list[NewRow]] = field(default_factory=dict)  # anchor base row -> rows
    sheet_name: str | None = None
    compresslevel: int = 6
    #: Row attributes (without r=/spans=) for rows inserted directly below the header.
    default_row_attrs: bytes = b""
    #: Last row number of the base sheet as measured from its data (the lookup index). Used instead
    #: of the declared ``<dimension>``, which files saved by other programs may leave stale or omit.
    base_last_row: int | None = None


@dataclass
class RewriteResult:
    rows_written: int = 0
    base_rows: int = 0
    inserted_rows: int = 0
    patched_rows: int = 0
    renumbered_rows: int = 0
    final_ref: str = ""
    inserted_row_numbers: dict[str, int] = field(default_factory=dict)  # NewRow.key -> output row
    updated_row_numbers: dict[int, int] = field(default_factory=dict)  # base row -> output row


def _col_map(base: list[str], final: list[str]) -> dict[int, int]:
    pos = {name: i + 1 for i, name in enumerate(final)}
    return {i + 1: pos[name] for i, name in enumerate(base)}


def _patch_cols(prologue: bytes, col_map: dict[int, int], spec: RewriteSpec, final_index: dict[str, int]) -> bytes:
    match = re.search(rb"<cols>(.*?)</cols>", prologue, re.S)
    new_cols = [n for n in spec.final_headers if n not in set(spec.base_headers)]
    if not match:
        return prologue
    per_col: dict[int, bytes] = {}
    for col in re.finditer(rb"<col\b([^>]*?)/>", match.group(1)):
        attrs = col.group(1)
        lo = int(re.search(rb'\bmin="(\d+)"', attrs).group(1))
        hi = int(re.search(rb'\bmax="(\d+)"', attrs).group(1))
        rest = re.sub(rb'\s*\b(min|max)="\d+"', b"", attrs)
        for i in range(lo, hi + 1):
            per_col[col_map.get(i, i + (len(spec.final_headers) - len(spec.base_headers)))] = rest
    for name in new_cols:
        width = spec.widths.get(name)
        if width:
            per_col[final_index[name]] = f' width="{width}" customWidth="1"'.encode()
    parts = []
    keys = sorted(per_col)
    i = 0
    while i < len(keys):
        j = i
        while j + 1 < len(keys) and keys[j + 1] == keys[j] + 1 and per_col[keys[j + 1]] == per_col[keys[i]]:
            j += 1
        parts.append(b'<col min="%d" max="%d"%s/>' % (keys[i], keys[j], per_col[keys[i]]))
        i = j + 1
    return prologue[: match.start(1)] + b"".join(parts) + prologue[match.end(1):]


def _row_open(num: int, attrs: bytes) -> bytes:
    rest = ROW_SPANS_RE.sub(b"", ROW_R_RE.sub(b"", attrs))
    return b'<row r="%d"%s>' % (num, rest)


def _retarget(raw_cell: bytes, letters: str, row: int) -> bytes:
    new_ref = f'r="{letters}{row}"'.encode()
    head_end = raw_cell.find(b">")
    head = CELL_R_ATTR_RE.sub(new_ref, raw_cell[: head_end + 1], count=1)
    if head == raw_cell[: head_end + 1] and b' r="' not in head:
        head = head.replace(b"<c", b"<c " + new_ref, 1)
    return head + raw_cell[head_end + 1:]


def rewrite_workbook(
    base_path: str,
    out_path: str,
    spec: RewriteSpec,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> RewriteResult:
    info = read_workbook_info(base_path)
    if len(info.sheets) != 1:
        raise UnsafeWorkbookError(f"Expected exactly one worksheet, found {len(info.sheets)}")
    sheet = info.sheet(spec.sheet_name)
    final_index = {name: i + 1 for i, name in enumerate(spec.final_headers)}
    col_map = _col_map(spec.base_headers, spec.final_headers)
    columns_shift = any(k != v for k, v in col_map.items())
    first_shifted = min((k for k, v in col_map.items() if k != v), default=10**9)
    n_inserts = sum(len(v) for v in spec.inserts.values())
    structural = columns_shift or n_inserts > 0
    if len(spec.final_headers) > EXCEL_MAX_COLS:
        raise UnsafeWorkbookError(f"{len(spec.final_headers)} columns exceed Excel's limit of {EXCEL_MAX_COLS}")
    result = RewriteResult()

    def sheet_bytes(fh: Any) -> None:
        stream = SheetStream(base_path, sheet.member, progress)
        rows = stream.rows()
        header = next(rows, None)
        if header is None or header.num != 1:
            raise UnsafeWorkbookError("Warehouse header row (row 1) not found")
        prologue = stream.prologue
        base_last = spec.base_last_row or _base_last_row(prologue)
        final_rows = base_last + n_inserts
        if final_rows > EXCEL_MAX_ROWS:
            raise UnsafeWorkbookError(
                f"Result would need {final_rows:,} rows; one worksheet holds at most {EXCEL_MAX_ROWS:,}. "
                "Nothing was written — the warehouse cannot remain a single sheet."
            )
        last_col = col_letter(len(spec.final_headers))
        result.final_ref = f"A1:{last_col}{final_rows}"
        prologue = _set_dimension(prologue, result.final_ref)
        if columns_shift:
            prologue = _patch_cols(prologue, col_map, spec, final_index)
        fh.write(prologue)
        # ---- header row ------------------------------------------------------
        hcells = {col_map[c.col]: _retarget(c.raw, col_letter(col_map[c.col]), 1) for c in header.cells()}
        for name in spec.final_headers:
            idx = final_index[name]
            if idx not in hcells:
                hcells[idx] = cell_xml(col_letter(idx), 1, CellWrite(name, "string", spec.header_styles.get(name)))
        fh.write(_row_open(1, header.attrs) + b"".join(hcells[k] for k in sorted(hcells)) + b"</row>")
        result.rows_written = 1
        shift = 0
        shift = _write_inserts(fh, 1, header, shift, spec, final_index, result)
        count = 0
        for row in rows:
            count += 1
            if cancelled is not None and count % 2000 == 0 and cancelled():
                raise OperationCancelled("Cancelled by user; the verified warehouse was not modified")
            new_num = row.num + shift
            updates = spec.updates.get(row.num)
            if structural and row.inner and b"<f" in row.inner:
                raise UnsafeWorkbookError(f"Row {row.num} contains formulas; structural edits are refused")
            if updates or (columns_shift and row.last_col() >= first_shifted):
                fh.write(_rebuild_row(row, new_num, updates or {}, col_map, final_index))
                result.patched_rows += 1
                if updates:
                    result.updated_row_numbers[row.num] = new_num
            elif shift:
                template = b"\\g<1>" + str(new_num).encode() + b'"'
                fh.write(_row_open(new_num, row.attrs) + CELL_REF_RE.sub(template, row.inner or b"") + b"</row>")
                result.renumbered_rows += 1
            else:
                fh.write(row.raw)
            result.rows_written += 1
            shift = _write_inserts(fh, row.num, row, shift, spec, final_index, result)
        result.base_rows = count + 1
        epilogue = stream.epilogue
        if structural and any(tok in epilogue for tok in UNSUPPORTED_EPILOGUE):
            raise UnsafeWorkbookError("Sheet contains merged cells/conditional formats/tables; structural edits refused")
        epilogue = AUTOFILTER_REF_RE.sub(lambda m: m.group(1) + result.final_ref.encode() + m.group(3), epilogue, count=1)
        fh.write(epilogue)

    sheet_info = None
    deferred_workbook: tuple[zipfile.ZipInfo, bytes] | None = None
    with zipfile.ZipFile(base_path) as zin, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=spec.compresslevel) as zout:
        for item in zin.infolist():
            target = zipfile.ZipInfo(item.filename, date_time=item.date_time)
            target.compress_type = zipfile.ZIP_DEFLATED
            target.external_attr = item.external_attr
            if item.filename == sheet.member:
                sheet_info = item
                force64 = item.file_size > 1_500_000_000
                with zout.open(target, "w", force_zip64=force64) as fh:
                    sheet_bytes(fh)
            elif item.filename == "xl/workbook.xml":
                deferred_workbook = (target, zin.read(item))  # patched once the final range is known
            else:
                zout.writestr(target, zin.read(item), compresslevel=spec.compresslevel)
        if deferred_workbook is not None:
            target, data = deferred_workbook
            zout.writestr(target, _patch_filter_name(data, sheet.name, result.final_ref), compresslevel=spec.compresslevel)
    if sheet_info is None:
        raise UnsafeWorkbookError("Worksheet part missing from workbook")
    return result


def _base_last_row(prologue: bytes) -> int:
    m = DIMENSION_REF_RE.search(prologue)
    last = re.search(rb"(\d+)$", m.group(2)) if m else None
    if not last:
        raise UnsafeWorkbookError("Worksheet has no <dimension> range; cannot validate row limits")
    return int(last.group(1))


def _set_dimension(prologue: bytes, ref: str) -> bytes:
    """Point ``<dimension>`` at the final range whatever its formatting; add the element when it is missing."""
    patched, found = DIMENSION_REF_RE.subn(lambda m: m.group(1) + ref.encode() + m.group(3), prologue, count=1)
    if found:
        return patched
    anchor = re.search(rb"<sheetPr\b[^>]*/>|</sheetPr>", prologue) or re.search(rb"<worksheet\b[^>]*>", prologue)
    if anchor is None:
        raise UnsafeWorkbookError("Worksheet start tag not found")
    return prologue[: anchor.end()] + f'<dimension ref="{ref}"/>'.encode() + prologue[anchor.end():]


def _write_inserts(fh: Any, anchor: int, anchor_row: RawRow, shift: int, spec: RewriteSpec,
                   final_index: dict[str, int], result: RewriteResult) -> int:
    rows = spec.inserts.get(anchor)
    if not rows:
        return shift
    attrs = anchor_row.attrs if anchor != 1 else spec.default_row_attrs
    for new_row in sorted(rows, key=lambda r: r.key):
        shift += 1
        num = anchor + shift
        cells = []
        for name, write in sorted(new_row.cells.items(), key=lambda kv: final_index[kv[0]]):
            if write.value is None or write.value == "":
                continue
            cells.append(cell_xml(col_letter(final_index[name]), num, write))
        fh.write(_row_open(num, attrs) + b"".join(cells) + b"</row>")
        result.inserted_rows += 1
        result.rows_written += 1
        result.inserted_row_numbers[new_row.key] = num
    return shift


def _rebuild_row(row: RawRow, new_num: int, updates: dict[str, CellWrite], col_map: dict[int, int],
                 final_index: dict[str, int]) -> bytes:
    cells: dict[int, bytes] = {}
    styles: dict[int, bytes | None] = {}
    for cell in parse_cells(row.inner or b""):
        idx = col_map[cell.col]
        cells[idx] = _retarget(cell.raw, col_letter(idx), new_num)
        styles[idx] = cell.style
    for name, write in updates.items():
        idx = final_index[name]
        if write.value is None:
            continue
        cells[idx] = cell_xml(col_letter(idx), new_num, write, keep_style=styles.get(idx))
    return _row_open(new_num, row.attrs) + b"".join(cells[k] for k in sorted(cells)) + b"</row>"


def _patch_filter_name(workbook_xml: bytes, sheet_name: str, final_ref: str) -> bytes:
    if not final_ref:
        return workbook_xml
    start, end = final_ref.split(":")
    s_col, s_row = re.match(r"([A-Z]+)(\d+)", start).groups()
    e_col, e_row = re.match(r"([A-Z]+)(\d+)", end).groups()
    quoted = sheet_name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", sheet_name) else "'" + sheet_name.replace("'", "''") + "'"
    target = f"{quoted}!${s_col}${s_row}:${e_col}${e_row}".encode()
    pattern = re.compile(rb'(<definedName name="_xlnm\._FilterDatabase"[^>]*>)([^<]*)(</definedName>)')
    return pattern.sub(lambda m: m.group(1) + target + m.group(3), workbook_xml, count=1)
