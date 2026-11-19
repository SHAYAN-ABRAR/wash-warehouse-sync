"""Minimal streaming XLSX creator (synthetic warehouses, fixtures, demo exports).

Writes valid single- or multi-sheet workbooks with inline strings, optional frozen
header, auto-filter and column widths — without holding rows in memory.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..numbers import excel_number_text
from ..schema import col_letter
from .writer import escape_text

#: Styles mirroring the warehouse's conventions: 0 default, 1-6 namespace header
#: fills, 6 centered data, 7 highlighted data. Colors are generic formatting only.
WAREHOUSE_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="3"><font><sz val="11"/><color rgb="FF000000"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
    '<font><sz val="11"/><color rgb="FF1F624D"/><name val="Calibri"/><family val="2"/></font></fonts>'
    '<fills count="7"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF253D57"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF237A72"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF4E658D"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF7960A8"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF2A7565"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="8"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    + "".join(
        f'<xf numFmtId="0" fontId="1" fillId="{fill}" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
        '<alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        for fill in (2, 3, 4, 5, 6)
    )
    + '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleLight16"/>'
    "</styleSheet>"
)

PLAIN_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="1"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


@dataclass
class SheetSpec:
    name: str
    headers: Sequence[str]
    rows: Iterable[Sequence[Any]]
    header_styles: Sequence[str | None] | None = None
    data_styles: Sequence[str | None] | None = None
    widths: dict[int, float] = field(default_factory=dict)
    freeze: tuple[int, int] | None = None  # (columns, rows)
    autofilter: bool = False
    row_attrs: str = ""
    header_row_attrs: str = ""


def _cell(ref: str, value: Any, style: str | None) -> str:
    s = f' s="{style}"' if style else ""
    if isinstance(value, bool):
        value = "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{excel_number_text(value)}</v></c>'
    text = str(value)
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<c r="{ref}"{s} t="inlineStr"><is><t{space}>{escape_text(text)}</t></is></c>'


def _sheet_xml_stream(spec: SheetSpec, total_rows_hint: int | None) -> Iterable[bytes]:
    ncols = len(spec.headers)
    letters = [col_letter(i + 1) for i in range(max(ncols, 1))]
    dim_rows = total_rows_hint if total_rows_hint is not None else 1
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
             f'<dimension ref="A1:{letters[-1]}{dim_rows}"/>']
    if spec.freeze:
        xs, ys = spec.freeze
        tl = f"{col_letter(xs + 1)}{ys + 1}"
        parts.append(
            '<sheetViews><sheetView showGridLines="0" tabSelected="1" workbookViewId="0">'
            f'<pane xSplit="{xs}" ySplit="{ys}" topLeftCell="{tl}" activePane="bottomRight" state="frozen"/>'
            '<selection pane="bottomRight"/></sheetView></sheetViews>'
        )
    parts.append('<sheetFormatPr defaultRowHeight="18" customHeight="1"/>')
    if spec.widths:
        parts.append("<cols>" + "".join(
            f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in sorted(spec.widths.items())
        ) + "</cols>")
    parts.append("<sheetData>")
    yield "".join(parts).encode()
    hs = spec.header_styles or [None] * ncols
    yield (f'<row r="1"{spec.header_row_attrs}>' + "".join(
        _cell(f"{letters[i]}1", h, hs[i]) for i, h in enumerate(spec.headers)) + "</row>").encode()
    ds = spec.data_styles or [None] * ncols
    r = 1
    batch: list[str] = []
    for values in spec.rows:
        r += 1
        cells = []
        for i, value in enumerate(values):
            if value is None or value == "":
                continue
            cells.append(_cell(f"{letters[i]}{r}", value, ds[i] if i < len(ds) else None))
        batch.append(f'<row r="{r}"{spec.row_attrs}>{"".join(cells)}</row>')
        if len(batch) >= 1000:
            yield "".join(batch).encode()
            batch = []
    if batch:
        yield "".join(batch).encode()
    tail = "</sheetData>"
    if spec.autofilter:
        tail += f'<autoFilter ref="A1:{letters[-1]}{r}"/>'
    tail += '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>'
    yield tail.encode()
    if total_rows_hint is not None and total_rows_hint != r:
        raise ValueError(f"row count hint {total_rows_hint} != actual rows {r}")


def write_workbook(path: str, sheets: list[SheetSpec], styles_xml: str = PLAIN_STYLES_XML,
                   row_counts: list[int] | None = None, compresslevel: int = 6) -> None:
    """Write a workbook. ``row_counts`` (incl. header) lets the dimension be exact.

    When row counts are not provided, the rows are first materialized per sheet to
    count them (fine for fixtures; the scale generator always passes counts).
    """
    materialized: list[SheetSpec] = []
    counts: list[int] = []
    for i, spec in enumerate(sheets):
        if row_counts is None:
            rows = [list(r) for r in spec.rows]
            spec = SheetSpec(**{**spec.__dict__, "rows": rows})
            counts.append(len(rows) + 1)
        else:
            counts.append(row_counts[i])
        materialized.append(spec)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
          '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>']
    for i in range(len(materialized)):
        ct.append(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    ct.append("</Types>")
    sheets_xml = "".join(
        f'<sheet name="{escape_text(s.name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, s in enumerate(materialized)
    )
    defined = "".join(
        f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
        f"{escape_text(s.name)}!$A$1:${col_letter(len(s.headers))}${counts[i]}</definedName>"
        for i, s in enumerate(materialized) if s.autofilter
    )
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<sheets>{sheets_xml}</sheets>" + (f"<definedNames>{defined}</definedNames>" if defined else "")
                + '<calcPr calcId="124519" fullCalcOnLoad="1"/></workbook>')
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(len(materialized)):
        rels.append(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    f'relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>')
    rels.append(f'<Relationship Id="rId{len(materialized) + 1}" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                 'officeDocument" Target="xl/workbook.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
                 'core-properties" Target="docProps/core.xml"/>'
                 '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                 'extended-properties" Target="docProps/app.xml"/></Relationships>')
    titles = "".join(f"<vt:lpstr>{escape_text(s.name)}</vt:lpstr>" for s in materialized)
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
           'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
           '<Application>WASH Warehouse Sync</Application><HeadingPairs><vt:vector size="2" baseType="variant">'
           f'<vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant><vt:variant><vt:i4>{len(materialized)}</vt:i4>'
           f'</vt:variant></vt:vector></HeadingPairs><TitlesOfParts><vt:vector size="{len(materialized)}" '
           f'baseType="lpstr">{titles}</vt:vector></TitlesOfParts></Properties>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>WASH Warehouse Sync (synthetic)</dc:creator>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:created></cp:coreProperties>')
    fixed = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as zf:
        zf.writestr(zipfile.ZipInfo("[Content_Types].xml", fixed), "".join(ct))
        zf.writestr(zipfile.ZipInfo("_rels/.rels", fixed), root_rels)
        zf.writestr(zipfile.ZipInfo("xl/workbook.xml", fixed), workbook)
        zf.writestr(zipfile.ZipInfo("xl/_rels/workbook.xml.rels", fixed), "".join(rels))
        zf.writestr(zipfile.ZipInfo("xl/styles.xml", fixed), styles_xml)
        zf.writestr(zipfile.ZipInfo("docProps/app.xml", fixed), app)
        zf.writestr(zipfile.ZipInfo("docProps/core.xml", fixed), core)
        for i, spec in enumerate(materialized):
            info = zipfile.ZipInfo(f"xl/worksheets/sheet{i + 1}.xml", fixed)
            info.compress_type = zipfile.ZIP_DEFLATED
            with zf.open(info, "w", force_zip64=counts[i] > 300_000) as fh:
                for chunk in _sheet_xml_stream(spec, counts[i]):
                    fh.write(chunk)
