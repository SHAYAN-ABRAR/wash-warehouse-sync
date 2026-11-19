"""Shared adapter infrastructure: context, detection and common field helpers."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..admin import AdminResolver, AliasTable
from ..records import SourceRecord
from ..schema import Period
from ..text import clean, norm_header
from .tabular import HeaderMap, Table, list_sheets, load_profiles, read_table

SOURCES = ("GPANI", "WPMS", "AQUAWATCH", "BORELOG")


@dataclass
class AdapterContext:
    admin: AdminResolver
    file_name: str
    file_path: str
    strict: bool = False
    forced_period: Period | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Detection:
    source: str | None
    format: str | None
    sheet: str | None
    confidence: float
    matched: dict[str, str] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    personal_data_columns: list[str] = field(default_factory=list)
    sheets: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# Well type and survey period
# ---------------------------------------------------------------------------

def normalize_well_type(value: Any) -> tuple[str | None, str | None]:
    """Return ``(comparison_key, display)``.

    ``Deep`` and ``Moderately Deep`` are distinct classes and are never conflated.
    """
    text = clean(value)
    if text is None:
        return None, None
    low = text.lower()
    if low in ("none", "null"):
        return None, None
    if re.search(r"\bmoderate(ly)?[\s-]*deep\b|\bmod\.?\s*deep\b", low):
        return "MODERATELY DEEP", "Moderately Deep"
    if re.search(r"\bvery\s+deep\b", low):
        return "VERY DEEP", "Very Deep"
    if re.search(r"\bdeep\b|\bdtw\b", low):
        return "DEEP", "Deep" if low in ("deep", "dtw") else text
    if re.search(r"\bshallow\b|\bstw\b", low):
        return "SHALLOW", "Shallow" if low in ("shallow", "stw") else text
    if "rain" in low and "harvest" in low:
        return "RWH", text
    if "community" in low:
        return "COMMUNITY", text
    return "OTHER", text


_PERIOD_PATTERNS = [
    re.compile(r"^\s*(?P<year>(?:19|20)\d{2})\s*(?:\(\s*(?P<s1>wet|dry)\s*(?:season)?\s*\)|[-_ /]*(?P<s2>wet|dry)(?:\s*season)?)?\s*$", re.I),
    re.compile(r"^\s*(?P<s3>wet|dry)\s*(?:season)?\s*[-_ /,]*\s*(?P<year>(?:19|20)\d{2})\s*$", re.I),
]


def parse_survey_period(value: Any) -> tuple[Period | None, str | None]:
    """Parse ``"2026 (Wet Season)"``, ``"2025"``, ``"Dry 2027"``, ``2024.0`` …

    Returns ``(period, problem)``. Unknown season words are rejected rather than
    guessed, so wet- and dry-season data can never be confused.
    """
    text = clean(value)
    if text is None:
        return None, "Survey year is blank"
    text = re.sub(r"\.0+$", "", text)
    for pattern in _PERIOD_PATTERNS:
        m = pattern.match(text)
        if m:
            season = next((m.group(g) for g in ("s1", "s2", "s3") if g in pattern.groupindex and m.group(g)), None)
            year = int(m.group("year"))
            return Period(year, season.lower() if season else None), None
    return None, f"Unrecognized survey year/season label {text!r}"


def json_admin(raw: dict[str, Any], compact: bool) -> str:
    import json  # noqa: PLC0415

    data = {k: v for k, v in raw.items() if v is not None}
    if compact:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _known_headers() -> set[str]:
    out: set[str] = set()
    for source in load_profiles()["sources"].values():
        for fmt in source["formats"].values():
            fields = fmt["fields"]
            groups = fields.values() if not any(isinstance(v, dict) for v in fields.values()) else [
                a for sub in fields.values() for a in sub.values()]
            for aliases in groups:
                out.update(norm_header(a) for a in aliases)
    return out


def detect_source(path: str, hint: str | None = None) -> Detection:
    """Identify the source system and export format of a file by its headers."""
    profiles = load_profiles()["sources"]
    sheets = list_sheets(path)
    norm_sheets = {norm_header(s): s for s in sheets}
    bore = profiles["BORELOG"]["formats"]["borelog_workbook"]
    found_tabs = {
        tab: next((norm_sheets[norm_header(a)] for a in aliases if norm_header(a) in norm_sheets), None)
        for tab, aliases in bore["sheets"].items()
    }
    if found_tabs["general"] and sum(1 for v in found_tabs.values() if v) >= 3 and hint in (None, "BORELOG"):
        table = read_table(path, found_tabs["general"], _known_headers())
        hm = HeaderMap(table.headers, bore["fields"]["general"])
        missing_tabs = [t for t, v in found_tabs.items() if not v]
        return Detection(
            "BORELOG", "borelog_workbook", found_tabs["general"],
            confidence=1.0 if not missing_tabs else 0.8,
            matched=hm.matched_header, missing_required=[] if hm.has("id") else ["id"],
            missing_optional=[f"tab:{t}" for t in missing_tabs] + [m for m in hm.missing if m != "id"],
            ignored_columns=hm.ignored, personal_data_columns=hm.personal_data, sheets=sheets,
            reason=f"bore-log workbook with tabs {[v for v in found_tabs.values() if v]}",
        )
    best: Detection | None = None
    for sheet in sheets[:6]:
        sheet_arg = None if path.lower().endswith((".csv", ".txt")) else sheet
        table = read_table(path, sheet_arg, _known_headers())
        if not table.headers:
            continue
        for source, spec in profiles.items():
            if source == "BORELOG" or (hint and hint != source):
                continue
            for fmt_name, fmt in spec["formats"].items():
                hm = HeaderMap(table.headers, fmt["fields"])
                required = fmt.get("required", [])
                missing_req = [r for r in required if not hm.has(r)]
                if fmt.get("year_columns_regex"):
                    years = [h for h in table.headers if re.match(fmt["year_columns_regex"], h.strip())]
                    if len(years) < fmt.get("min_year_columns", 1):
                        missing_req.append("year_columns")
                matched = len(hm.index)
                total = len(fmt["fields"])
                score = (matched / total) if not missing_req else 0.0
                if fmt.get("signature"):
                    sig_hits = sum(1 for s in fmt["signature"] if norm_header(s) in {norm_header(h) for h in table.headers})
                    score += 0.1 * sig_hits
                det = Detection(
                    source, fmt_name, sheet_arg, round(min(score, 1.0), 3), hm.matched_header, missing_req,
                    [m for m in hm.missing if m not in required], hm.ignored, hm.personal_data, sheets,
                    reason=f"{matched}/{total} known columns matched on sheet {table.display_sheet!r}",
                )
                if best is None or det.confidence > best.confidence:
                    best = det
    if best is None or best.confidence <= 0:
        return Detection(None, None, None, 0.0, sheets=sheets,
                         reason="No supported source schema recognized (check that the header row is present)")
    return best


def read_records(path: str, ctx: AdapterContext, detection: Detection | None = None) -> Iterator[SourceRecord]:
    """Dispatch to the adapter for the detected format."""
    from . import aquawatch, borelog, gpani, wpms  # noqa: PLC0415

    det = detection or detect_source(path)
    if det.source is None or det.missing_required:
        raise ValueError(f"{os.path.basename(path)}: cannot read records ({det.reason}; missing {det.missing_required})")
    profiles = load_profiles()["sources"]
    fmt = profiles[det.source]["formats"][det.format]
    if det.format == "borelog_workbook":
        yield from borelog.read(path, ctx, fmt)
        return
    table = read_table(path, det.sheet, _known_headers())
    reader = {
        "aquawatch_survey": aquawatch.read_survey,
        "aquawatch_historical": aquawatch.read_historical,
        "gpani_tubewell_report": gpani.read,
        "gpani_community": gpani.read,
        "wpms_tubewell_list": wpms.read,
    }[det.format]
    yield from reader(table, ctx, fmt, det.format)


def default_resolver(aliases: list[dict[str, Any]] | None = None) -> AdminResolver:
    from ..admin import DEFAULT_ALIASES  # noqa: PLC0415

    return AdminResolver(AliasTable.from_config(aliases or DEFAULT_ALIASES))


__all__ = ["AdapterContext", "Detection", "detect_source", "read_records", "normalize_well_type",
           "parse_survey_period", "Table", "SOURCES"]
