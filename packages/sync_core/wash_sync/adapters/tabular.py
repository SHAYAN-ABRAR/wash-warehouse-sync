"""Header-based tabular readers for .xls (BIFF), .xlsx and .csv source exports.

Values are returned as text exactly as displayed by the source (integral numbers
without a spurious ``.0``, dates in ISO format). Columns are later resolved by
header names, so column order may vary between exports.
"""

from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from importlib import resources
from typing import Any

from ..text import norm_header, to_text
from ..xlsx.reader import SheetStream, load_date_styles, load_shared_strings, read_workbook_info

SUPPORTED_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv", ".txt")


@lru_cache(maxsize=1)
def load_profiles() -> dict[str, Any]:
    with resources.files("wash_sync.resources").joinpath("source_profiles.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def personal_data_headers() -> set[str]:
    return {norm_header(h) for h in load_profiles()["personal_data_columns"]}


@dataclass
class Table:
    """A located header row plus a *re-iterable* stream of data rows.

    Rows are never all held in memory, so very large exports (e.g. a full GPANI
    registry) can be processed on ordinary hardware. ``rows()`` re-reads the file.
    """

    path: str
    sheet: str | None
    headers: list[str]
    header_row: int

    def rows(self) -> Iterator[tuple[int, list[str | None]]]:
        if not self.headers:
            return
        for num, values in iter_rows(self.path, self.sheet):
            if num <= self.header_row:
                continue
            if any(v for v in values):
                yield num, values

    @property
    def display_sheet(self) -> str:
        return self.sheet or os.path.basename(self.path)


def list_sheets(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xls":
        import xlrd  # noqa: PLC0415

        book = xlrd.open_workbook(path, on_demand=True)
        try:
            return book.sheet_names()
        finally:
            book.release_resources()
    if ext in (".xlsx", ".xlsm"):
        return [s.name for s in read_workbook_info(path).sheets]
    return [os.path.basename(path)]


def _xlsx_rows(path: str, sheet: str | None) -> Iterator[tuple[int, list[str | None]]]:
    info = read_workbook_info(path)
    ref = info.sheet(sheet)
    with zipfile.ZipFile(path) as zf:
        shared = load_shared_strings(zf, info.shared_strings_member)
        date_styles = load_date_styles(zf, info.styles_member)
    for row in SheetStream(path, ref.member).rows():
        cells = row.cells(shared)
        if not cells:
            yield row.num, []
            continue
        width = max(c.col for c in cells)
        values: list[str | None] = [None] * width
        for c in cells:
            v: Any = c.value
            if c.type == "n" and isinstance(v, (int, float)) and c.style and int(c.style) in date_styles:
                v = _excel_serial_to_iso(float(v))
            values[c.col - 1] = to_text(v)
        yield row.num, values


def _excel_serial_to_iso(serial: float) -> str:
    dt = datetime(1899, 12, 30) + timedelta(days=serial)
    return dt.strftime("%Y-%m-%d") if dt.hour == dt.minute == dt.second == 0 else dt.isoformat(sep=" ")


def _xls_rows(path: str, sheet: str | None) -> Iterator[tuple[int, list[str | None]]]:
    import xlrd  # noqa: PLC0415

    book = xlrd.open_workbook(path, on_demand=True)
    try:
        sh = book.sheet_by_name(sheet) if sheet else book.sheet_by_index(0)
        for r in range(sh.nrows):
            values: list[str | None] = []
            for c in range(sh.ncols):
                ctype = sh.cell_type(r, c)
                value = sh.cell_value(r, c)
                if ctype == xlrd.XL_CELL_DATE:
                    try:
                        dt = xlrd.xldate.xldate_as_datetime(value, book.datemode)
                        value = dt.strftime("%Y-%m-%d") if (dt.hour, dt.minute, dt.second) == (0, 0, 0) else dt.isoformat(sep=" ")
                    except (ValueError, OverflowError):
                        pass
                elif ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    value = None
                elif ctype == xlrd.XL_CELL_ERROR:
                    value = None
                values.append(to_text(value))
            yield r + 1, values
    finally:
        book.release_resources()


def _csv_rows(path: str) -> Iterator[tuple[int, list[str | None]]]:
    with open(path, "rb") as fh:
        data = fh.read()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        yield i + 1, [to_text(v) for v in row]


def iter_rows(path: str, sheet: str | None = None) -> Iterator[tuple[int, list[str | None]]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return _xlsx_rows(path, sheet)
    if ext == ".xls":
        return _xls_rows(path, sheet)
    if ext in (".csv", ".txt"):
        return _csv_rows(path)
    raise ValueError(f"Unsupported file type {ext!r}; supported: {', '.join(SUPPORTED_EXTENSIONS)}")


def estimate_rows(path: str, sheet: str | None = None) -> int | None:
    """Cheap row-count estimate used for real progress reporting."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import re  # noqa: PLC0415

        info = read_workbook_info(path)
        with zipfile.ZipFile(path) as zf, zf.open(info.sheet(sheet).member) as fh:
            head = fh.read(65536)
        m = re.search(rb'<dimension ref="[A-Z]+\d+:[A-Z]+(\d+)"', head)
        return int(m.group(1)) if m else None
    if ext == ".xls":
        import xlrd  # noqa: PLC0415

        book = xlrd.open_workbook(path, on_demand=True)
        try:
            sh = book.sheet_by_name(sheet) if sheet else book.sheet_by_index(0)
            return sh.nrows
        finally:
            book.release_resources()
    if ext in (".csv", ".txt"):
        with open(path, "rb") as fh:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 20), b""))
    return None


def read_table(path: str, sheet: str | None = None, known_headers: set[str] | None = None, scan_rows: int = 15) -> Table:
    """Locate the header row among the first ``scan_rows`` rows of a sheet.

    The header row is the row (with at least two non-empty cells) that matches the
    most known header aliases; without known headers, the first dense row is used.
    """
    head: list[tuple[int, list[str | None]]] = []
    for item in iter_rows(path, sheet):
        head.append(item)
        if len(head) >= scan_rows:
            break
    best_idx, best_score = None, -1
    for i, (_num, values) in enumerate(head):
        nonempty = [v for v in values if v]
        if len(nonempty) < 2:
            continue
        score = sum(1 for v in nonempty if known_headers and norm_header(v) in known_headers)
        if best_idx is None or score > best_score:
            best_idx, best_score = i, score
        if known_headers is None:
            break
    if best_idx is None:
        return Table(path, sheet, [], 0)
    header_num, header_vals = head[best_idx]
    headers = [to_text(h) or f"column_{i + 1}" for i, h in enumerate(header_vals)]
    return Table(path, sheet, headers, header_num)


class HeaderMap:
    """Resolve logical fields to column positions by header aliases."""

    def __init__(self, headers: list[str], fields: dict[str, list[str]]) -> None:
        normalized: dict[str, int] = {}
        for i, h in enumerate(headers):
            normalized.setdefault(norm_header(h), i)
        self.index: dict[str, int] = {}
        self.matched_header: dict[str, str] = {}
        for name, aliases in fields.items():
            for alias in aliases:
                i = normalized.get(norm_header(alias))
                if i is not None and i not in self.index.values():
                    self.index[name] = i
                    self.matched_header[name] = headers[i]
                    break
        pii = personal_data_headers()
        used = set(self.index.values())
        self.ignored = [h for i, h in enumerate(headers) if i not in used and norm_header(h) not in pii]
        self.personal_data = [h for h in headers if norm_header(h) in pii]
        self.missing = [name for name in fields if name not in self.index]

    def get(self, values: list[str | None], name: str) -> str | None:
        i = self.index.get(name)
        if i is None or i >= len(values):
            return None
        return values[i]

    def has(self, name: str) -> bool:
        return name in self.index
