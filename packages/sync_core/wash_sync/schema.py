"""Warehouse schema knowledge: namespaces, AquaWatch period columns and column placement.

The *actual* header row of the workbook is always the source of truth; this module
only encodes the naming conventions observed in the verified warehouse so that new
year/season columns can be created consistently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CORE_COLUMNS = (
    "candidate_id", "division", "district", "upazila", "union_name", "village_name",
    "facility_type", "latitude", "longitude", "record_sources", "match_status",
    "match_evidence", "quality_flags",
)
NAMESPACES = ("gpani_", "wpms_", "aqua_", "borelog_")

#: Excel hard limits for a single worksheet.
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_COLS = 16_384
EXCEL_MAX_CELL_CHARS = 32_767

SEASONS = ("dry", "wet")
#: Metrics stored per AquaWatch survey period, in warehouse column order.
AQUA_PERIOD_METRICS = ("arsenic_raw", "water_table_m", "iron_raw", "chloride_raw", "source_row")

AQUA_PERIOD_RE = re.compile(
    r"^aqua_(?P<year>\d{4})(?:_(?P<season>wet|dry))?_(?P<metric>arsenic_raw|water_table_m|iron_raw|chloride_raw|source_row)$"
)
AQUA_HISTORICAL_RE = re.compile(r"^aqua_(?P<year>\d{4})$")


@dataclass(frozen=True, slots=True, order=True)
class Period:
    """An AquaWatch survey period. ``season`` is ``None`` for unsuffixed annual data.

    The 2025 unsuffixed period and the explicitly labelled 2025 wet season are
    distinct periods and therefore distinct column groups.
    """

    year: int
    season: str | None = None

    @property
    def column_prefix(self) -> str:
        return f"aqua_{self.year}" + (f"_{self.season}" if self.season else "")

    @property
    def source_token(self) -> str:
        return f"AQUA_{self.year}" + (f"_{self.season.upper()}" if self.season else "")

    @property
    def candidate_prefix(self) -> str:
        return f"AQUA{self.year}" + (self.season.upper() if self.season else "")

    @property
    def label(self) -> str:
        return f"{self.year}" + (f" ({self.season.capitalize()} Season)" if self.season else " (annual, season unspecified)")

    @property
    def key(self) -> str:
        return f"{self.year}_{self.season or 'annual'}"

    def columns(self) -> list[str]:
        return [f"{self.column_prefix}_{metric}" for metric in AQUA_PERIOD_METRICS]

    def column(self, metric: str) -> str:
        return f"{self.column_prefix}_{metric}"


def parse_period_column(name: str) -> tuple[Period, str] | None:
    match = AQUA_PERIOD_RE.match(name)
    if not match:
        return None
    return Period(int(match.group("year")), match.group("season")), match.group("metric")


def namespace_of(name: str) -> str:
    for ns in NAMESPACES:
        if name.startswith(ns):
            return ns.rstrip("_")
    return "core"


def col_letter(index: int) -> str:
    """1-based column index → Excel letters (1 → A, 196 → GN)."""
    if index < 1:
        raise ValueError("column index must be >= 1")
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def col_index(letters: str) -> int:
    value = 0
    for ch in letters:
        value = value * 26 + (ord(ch) - 64)
    return value


@dataclass(slots=True)
class NewColumn:
    name: str
    insert_after: str  # existing (or earlier new) column name
    header_style: str | None
    width: float | None
    value_type: str  # "number" | "string" | "auto"
    reason: str


@dataclass
class WarehouseSchema:
    """Column layout of one warehouse version (built from the real header row)."""

    headers: list[str]
    header_styles: dict[str, str | None] = field(default_factory=dict)
    column_types: dict[str, str] = field(default_factory=dict)  # dominant data type
    column_styles: dict[str, str | None] = field(default_factory=dict)  # dominant data style
    widths: dict[int, float] = field(default_factory=dict)  # 1-based index -> width

    def __post_init__(self) -> None:
        self._index = {name: i + 1 for i, name in enumerate(self.headers)}
        if len(self._index) != len(self.headers):
            dupes = sorted({h for h in self.headers if self.headers.count(h) > 1})
            raise ValueError(f"Duplicate warehouse headers: {dupes}")

    def index(self, name: str) -> int:
        return self._index[name]

    def has(self, name: str) -> bool:
        return name in self._index

    @property
    def periods(self) -> list[Period]:
        found: dict[Period, None] = {}
        for name in self.headers:
            parsed = parse_period_column(name)
            if parsed:
                found.setdefault(parsed[0], None)
        return list(found)

    def has_period(self, period: Period) -> bool:
        return all(self.has(c) for c in period.columns())

    def template_period_for_new(self) -> Period | None:
        """Most recent existing period group; its cell conventions are reused."""
        periods = self.periods
        return periods[-1] if periods else None

    def plan_period_columns(self, period: Period, already_planned: list[NewColumn]) -> list[NewColumn]:
        """Columns needed for a new survey period, placed chronologically.

        The group is inserted after the last column of the latest existing period
        whose year is <= the new year (or after earlier planned groups), keeping the
        AquaWatch block contiguous and ahead of the ``borelog_*`` namespace.
        """
        planned_names = {c.name for c in already_planned}
        missing = [c for c in period.columns() if not self.has(c) and c not in planned_names]
        if not missing:
            return []
        if any(self.has(c) for c in period.columns()):
            raise ValueError(f"Period {period.key} is only partially present in the warehouse")
        anchor = None
        for name in self.headers:
            parsed = parse_period_column(name)
            if parsed and parsed[0].year <= period.year:
                anchor = name
        for col in already_planned:
            parsed = parse_period_column(col.name)
            if parsed and parsed[0].year <= period.year:
                anchor = col.name
        if anchor is None:
            aqua_cols = [h for h in self.headers if h.startswith("aqua_")]
            anchor = aqua_cols[-1] if aqua_cols else self.headers[-1]
        template = self.template_period_for_new()
        out: list[NewColumn] = []
        previous = anchor
        for metric in AQUA_PERIOD_METRICS:
            name = period.column(metric)
            header_style = None
            width = None
            value_type = "number" if metric in ("water_table_m", "source_row") else "auto"
            if template is not None and self.has(template.column(metric)):
                tcol = template.column(metric)
                header_style = self.header_styles.get(tcol)
                width = self.widths.get(self.index(tcol))
                ttype = self.column_types.get(tcol)
                if metric not in ("water_table_m", "source_row") and ttype:
                    value_type = "number" if ttype == "n" else "string"
            out.append(
                NewColumn(
                    name=name,
                    insert_after=previous,
                    header_style=header_style,
                    width=width,
                    value_type=value_type,
                    reason=f"New AquaWatch survey period {period.label}",
                )
            )
            previous = name
        return out


def final_column_order(headers: list[str], new_columns: list[NewColumn]) -> list[str]:
    """Apply planned insertions to the header list (deterministic order)."""
    order = list(headers)
    for col in new_columns:
        if col.name in order:
            continue
        pos = order.index(col.insert_after) + 1
        order.insert(pos, col.name)
    return order
