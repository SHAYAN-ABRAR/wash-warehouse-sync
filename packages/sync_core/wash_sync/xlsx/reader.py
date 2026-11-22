"""Streaming XLSX reader that never materializes the whole worksheet.

The sheet XML is read from the ZIP in bounded chunks and split into ``<row>``
elements with byte-level regular expressions. Each row keeps its exact original
bytes so that unchanged rows can be copied verbatim by the writer. Cell values are
decoded only on demand.
"""

from __future__ import annotations

import html
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ..schema import col_index

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}
CHUNK = 8 * 1024 * 1024

ROW_RE = re.compile(rb"<row\b([^>]*?)(?:/>|>(.*?)</row>)", re.S)
ROW_NUM_RE = re.compile(rb'\br="(\d+)"')
FAST_CELL_RE = re.compile(
    rb'<c r="([A-Z]{1,3})(\d+)"(?: s="(\d+)")?(?: t="([a-zA-Z]+)")?'
    rb'(?:/>|>(?:<v>([^<]*)</v>|<is><t(?: xml:space="preserve")?>([^<]*)</t></is>|<is><t(?: xml:space="preserve")?/></is>)</c>)'
)
GEN_CELL_RE = re.compile(rb"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.S)
ATTR_RE = re.compile(rb'([\w:]+)="([^"]*)"')
REF_RE = re.compile(rb"^([A-Z]{1,3})(\d+)$")
T_RE = re.compile(rb"<t\b[^>]*?(?:/>|>(.*?)</t>)", re.S)
V_RE = re.compile(rb"<v>(.*?)</v>", re.S)
RPH_RE = re.compile(rb"<rPh\b.*?</rPh>", re.S)
#: Matches the row-number part of a canonical cell reference (``<c r="AB12"``).
CELL_REF_STRIP_RE_SAFE = re.compile(rb'(<c r="[A-Z]{1,3})\d+"')
#: ``ref`` of ``<dimension>`` / ``<autoFilter>`` whatever the attribute order or spacing
#: (Excel writes ``<dimension ref="A1:B2"/>``, other tools ``<dimension ref="A1:B2" />``).
DIMENSION_REF_RE = re.compile(rb'(<dimension\b[^>]*?\bref=")([^"]*)(")')
AUTOFILTER_REF_RE = re.compile(rb'(<autoFilter\b[^>]*?\bref=")([^"]*)(")')
CELL_ROWNUM_RE = re.compile(rb'<c\b[^>]*?\br="[A-Z]{1,3}(\d+)"')
OOXML_ESC_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def xml_text(raw: bytes) -> str:
    """Decode XML text content (entities and OOXML ``_xHHHH_`` escapes)."""
    text = raw.decode("utf-8")
    if "&" in text:
        text = html.unescape(text)
    if "_x" in text:
        text = OOXML_ESC_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    return text


def parse_number_text(raw: bytes) -> int | float:
    text = raw.decode("ascii").strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return float(text)


class SharedRef(int):
    """Position in the shared-string table, returned when the table was not loaded.

    Workbooks saved by Excel store text this way. Callers that only move or compare raw cells
    (the streaming writer, the preservation proof between a version and its successor, which keeps
    the same table) need no text; anything that reads text loads the table and gets ``str``.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"SharedRef({int(self)})"


@dataclass(slots=True)
class Cell:
    col: int  # 1-based column index
    letters: bytes
    style: bytes | None
    type: str  # n | inlineStr | s | str | b | e
    value: str | int | float | None
    raw: bytes  # exact original cell XML


@dataclass(slots=True)
class RawRow:
    num: int
    attrs: bytes
    inner: bytes | None
    raw: bytes

    def cells(self, shared_strings: list[str] | None = None) -> list[Cell]:
        return parse_cells(self.inner or b"", shared_strings)

    def last_col(self) -> int:
        """Column index of the last cell (cheap: inspects only the final ``<c``)."""
        inner = self.inner or b""
        pos = inner.rfind(b'<c r="')
        if pos < 0:
            return 0
        end = pos + 6
        letters = bytearray()
        while end < len(inner) and 65 <= inner[end] <= 90:
            letters.append(inner[end])
            end += 1
        return col_index(letters.decode()) if letters else 0


def _decode(ctype: str, v: bytes | None, t: bytes | None, shared: list[str] | None) -> str | int | float | None:
    if ctype == "inlineStr":
        return xml_text(t) if t is not None else None
    if v is None:
        return None
    if ctype == "s":
        return SharedRef(int(v)) if shared is None else shared[int(v)]
    if ctype in ("str", "e"):
        return xml_text(v)
    if ctype == "b":
        return "TRUE" if v.strip() == b"1" else "FALSE"
    return parse_number_text(v)


def parse_cells(inner: bytes, shared_strings: list[str] | None = None) -> list[Cell]:
    """Parse all cells of a row. Uses a fast path for the canonical encodings."""
    if not inner:
        return []
    fast = list(FAST_CELL_RE.finditer(inner))
    total = inner.count(b"<c ") + inner.count(b"<c>")
    if len(fast) == total:
        out = []
        for m in fast:
            letters, _row, style, ctype, v, t = m.groups()
            ctype_s = ctype.decode() if ctype else "n"
            raw = m.group(0)
            if t is None and v is None:
                # <c .../> is an empty (possibly styled) cell; <is><t/></is> is an empty string
                value = "" if (ctype_s == "inlineStr" and raw.endswith(b"</c>")) else None
            else:
                value = _decode(ctype_s, v, t, shared_strings)
            out.append(Cell(col_index(letters.decode()), letters, style, ctype_s, value, m.group(0)))
        return out
    return _parse_cells_generic(inner, shared_strings)


def _parse_cells_generic(inner: bytes, shared_strings: list[str] | None) -> list[Cell]:
    out: list[Cell] = []
    next_col = 1
    for m in GEN_CELL_RE.finditer(inner):
        attrs = dict(ATTR_RE.findall(m.group(1)))
        body = m.group(2) or b""
        ref = attrs.get(b"r")
        if ref:
            rm = REF_RE.match(ref)
            letters = rm.group(1) if rm else b""
            col = col_index(letters.decode()) if letters else next_col
        else:
            col = next_col
            letters = b""
        next_col = col + 1
        ctype = (attrs.get(b"t") or b"n").decode()
        value: str | int | float | None
        if ctype == "inlineStr":
            body = RPH_RE.sub(b"", body)
            parts = [p or b"" for p in T_RE.findall(body)]
            value = xml_text(b"".join(parts)) if parts else None
        else:
            vm = V_RE.search(body)
            value = _decode(ctype, vm.group(1) if vm else None, None, shared_strings)
        out.append(Cell(col, letters, attrs.get(b"s"), ctype, value, m.group(0)))
    return out


@dataclass(slots=True)
class SheetRef:
    name: str
    member: str
    sheet_id: str
    rel_id: str


@dataclass(slots=True)
class WorkbookInfo:
    path: str
    sheets: list[SheetRef]
    defined_names: list[tuple[str, str | None, str]]
    shared_strings_member: str | None
    styles_member: str | None
    members: list[str]
    content_type_overrides: dict[str, str] = field(default_factory=dict)

    def sheet(self, name: str | None = None) -> SheetRef:
        if name is None:
            return self.sheets[0]
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        raise KeyError(f"Worksheet {name!r} not found; available: {[s.name for s in self.sheets]}")


def _resolve_target(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (base_dir + "/" + target).split("/")
    stack: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if stack:
                stack.pop()
        else:
            stack.append(part)
    return "/".join(stack)


def read_workbook_info(path: str) -> WorkbookInfo:
    with zipfile.ZipFile(path) as zf:
        members = zf.namelist()
        wb_member = "xl/workbook.xml"
        try:
            rels_root = ET.fromstring(zf.read("_rels/.rels"))
            for rel in rels_root.findall("pr:Relationship", NS):
                if rel.get("Type", "").endswith("/officeDocument"):
                    wb_member = _resolve_target("", rel.get("Target", wb_member))
        except KeyError:
            pass
        wb_root = ET.fromstring(zf.read(wb_member))
        wb_dir = wb_member.rsplit("/", 1)[0] if "/" in wb_member else ""
        rels_member = f"{wb_dir}/_rels/{wb_member.rsplit('/', 1)[-1]}.rels"
        targets: dict[str, tuple[str, str]] = {}
        if rels_member in members:
            for rel in ET.fromstring(zf.read(rels_member)).findall("pr:Relationship", NS):
                targets[rel.get("Id", "")] = (rel.get("Type", ""), _resolve_target(wb_dir, rel.get("Target", "")))
        sheets: list[SheetRef] = []
        for sh in wb_root.findall("m:sheets/m:sheet", NS):
            rid = sh.get(f"{{{NS['r']}}}id", "")
            sheets.append(SheetRef(sh.get("name", ""), targets.get(rid, ("", ""))[1], sh.get("sheetId", ""), rid))
        defined = [
            (dn.get("name", ""), dn.get("localSheetId"), dn.text or "")
            for dn in wb_root.findall("m:definedNames/m:definedName", NS)
        ]
        shared = next((t for (ty, t) in targets.values() if ty.endswith("/sharedStrings")), None)
        styles = next((t for (ty, t) in targets.values() if ty.endswith("/styles")), None)
        overrides: dict[str, str] = {}
        try:
            ct_root = ET.fromstring(zf.read("[Content_Types].xml"))
            for ov in ct_root.findall("ct:Override", NS):
                overrides[ov.get("PartName", "").lstrip("/")] = ov.get("ContentType", "")
        except KeyError:
            pass
    return WorkbookInfo(path, sheets, defined, shared, styles, members, overrides)


def load_shared_strings(zf: zipfile.ZipFile, member: str | None) -> list[str] | None:
    if not member or member not in zf.namelist():
        return None
    out: list[str] = []
    si_re = re.compile(rb"<si>(.*?)</si>|<si/>", re.S)
    buf = b""
    with zf.open(member) as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            buf += chunk
            last = buf.rfind(b"</si>")
            if last < 0:
                continue
            block, buf = buf[: last + 5], buf[last + 5:]
            for m in si_re.finditer(block):
                body = RPH_RE.sub(b"", m.group(1) or b"")
                out.append(xml_text(b"".join(p or b"" for p in T_RE.findall(body))))
        for m in si_re.finditer(buf):
            body = RPH_RE.sub(b"", m.group(1) or b"")
            out.append(xml_text(b"".join(p or b"" for p in T_RE.findall(body))))
    return out


_DATE_FMT_IDS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}


def load_date_styles(zf: zipfile.ZipFile, member: str | None) -> set[int]:
    """Indexes of ``cellXfs`` whose number format renders a date/time."""
    if not member or member not in zf.namelist():
        return set()
    root = ET.fromstring(zf.read(member))
    custom: dict[int, str] = {}
    for nf in root.findall("m:numFmts/m:numFmt", NS):
        custom[int(nf.get("numFmtId", "0"))] = nf.get("formatCode", "")
    dates: set[int] = set()
    for i, xf in enumerate(root.findall("m:cellXfs/m:xf", NS)):
        fmt_id = int(xf.get("numFmtId", "0"))
        if fmt_id in _DATE_FMT_IDS:
            dates.add(i)
        elif fmt_id in custom:
            code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", custom[fmt_id].lower())
            if re.search(r"[dmy]", code) and not re.search(r"[#0]", code.replace("0:", "")):
                dates.add(i)
    return dates


class SheetStream:
    """Iterate the rows of one worksheet member while capturing prologue/epilogue."""

    def __init__(self, path: str, member: str, progress: Callable[[float], None] | None = None) -> None:
        self.path = path
        self.member = member
        self.prologue = b""
        self.epilogue = b""
        self._progress = progress

    def rows(self) -> Iterator[RawRow]:
        with zipfile.ZipFile(self.path) as zf:
            total = max(1, zf.getinfo(self.member).file_size)
            consumed = 0
            with zf.open(self.member) as fh:
                buf = b""
                # --- prologue -------------------------------------------------
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        raise ValueError(f"{self.member}: <sheetData> not found")
                    consumed += len(chunk)
                    buf += chunk
                    pos = buf.find(b"<sheetData")
                    if pos >= 0:
                        end = buf.find(b">", pos)
                        if end >= 0:
                            break
                if buf[end - 1:end] == b"/":  # <sheetData/>: empty sheet
                    self.prologue = buf[: end + 1]
                    rest = buf[end + 1:]
                    rest += fh.read()
                    self.epilogue = rest
                    return
                self.prologue = buf[: end + 1]
                buf = buf[end + 1:]
                # --- rows -----------------------------------------------------
                while True:
                    stop = buf.find(b"</sheetData>")
                    if stop >= 0:
                        block, tail = buf[:stop], buf[stop:]
                        yield from self._split(block)
                        self.epilogue = tail + fh.read()
                        if self._progress:
                            self._progress(1.0)
                        return
                    last = buf.rfind(b"</row>")
                    if last >= 0:
                        block, buf = buf[: last + 6], buf[last + 6:]
                        yield from self._split(block)
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        raise ValueError(f"{self.member}: unterminated <sheetData>")
                    consumed += len(chunk)
                    buf += chunk
                    if self._progress:
                        self._progress(min(0.999, consumed / total))

    @staticmethod
    def _split(block: bytes) -> Iterator[RawRow]:
        for m in ROW_RE.finditer(block):
            attrs = m.group(1)
            rm = ROW_NUM_RE.search(attrs)
            if rm is None:
                raise ValueError("Row without an explicit r= attribute is not supported")
            yield RawRow(int(rm.group(1)), attrs, m.group(2), m.group(0))


def iter_values(path: str, sheet_name: str | None = None) -> Iterator[tuple[int, dict[int, object]]]:
    """Convenience iterator yielding ``(row_number, {col_index: value})``."""
    info = read_workbook_info(path)
    sheet = info.sheet(sheet_name)
    with zipfile.ZipFile(path) as zf:
        shared = load_shared_strings(zf, info.shared_strings_member)
    for row in SheetStream(path, sheet.member).rows():
        yield row.num, {c.col: c.value for c in row.cells(shared) if c.value is not None}
