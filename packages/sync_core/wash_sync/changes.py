"""Field-level change detection between an incoming record and a warehouse row."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .adapters.borelog import comparable_layers
from .plan import CellChange
from .records import Category, FieldValue, Policy, SourceRecord, canonical_value
from .schema import WarehouseSchema

JSON_LAYER_COLUMNS = {"borelog_lithology_profile_raw", "borelog_surface_observations_raw"}
_PLAIN_NUMBER = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T]00:00(?::00(?:\.0+)?)?)?$")


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def values_equal(column: str, old: Any, new: Any) -> bool:
    if column in JSON_LAYER_COLUMNS:
        return comparable_layers(old) == comparable_layers(new)
    if isinstance(old, str) and isinstance(new, str):
        mo, mn = _DATE.match(old.strip()), _DATE.match(new.strip())
        if mo and mn:
            return mo.group(1) == mn.group(1)
    return canonical_value(old) == canonical_value(new)


def layers_appended(old: Any, new: Any) -> bool:
    """True when the new profile keeps every old layer in order and only adds layers."""
    a, b = comparable_layers(old), comparable_layers(new)
    return len(b) > len(a) and b[: len(a)] == a


class CellTyper:
    """Decide numeric vs text cells by following each column's existing convention."""

    def __init__(self, schema: WarehouseSchema, new_column_types: dict[str, str]) -> None:
        self.schema = schema
        self.new_types = new_column_types

    def __call__(self, column: str, value: Any, value_type: str) -> tuple[str, Any]:
        if isinstance(value, bool):
            return "string", "TRUE" if value else "FALSE"
        numeric_text = isinstance(value, str) and _PLAIN_NUMBER.match(value.strip()) is not None
        if value_type == "number":
            if isinstance(value, (int, float)):
                return "number", value
            if numeric_text:
                return "number", float(value)
            return "string", str(value)
        if value_type == "string":
            return "string", value if isinstance(value, str) else _text(value)
        convention = self.new_types.get(column) or self.schema.column_types.get(column)
        if convention in ("n", "number"):
            if isinstance(value, (int, float)):
                return "number", value
            if numeric_text:
                return "number", float(value)
            return "string", str(value)
        return "string", value if isinstance(value, str) else _text(value)


def _text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


@dataclass
class Diff:
    fills: list[CellChange] = field(default_factory=list)
    sets: list[CellChange] = field(default_factory=list)
    piggyback: list[CellChange] = field(default_factory=list)
    appended_layers: list[CellChange] = field(default_factory=list)
    identity_fills: list[CellChange] = field(default_factory=list)
    fill_only_conflicts: list[tuple[str, Any, Any]] = field(default_factory=list)
    unchanged_fields: int = 0
    groups_new: set[str] = field(default_factory=set)
    groups_touched: set[str] = field(default_factory=set)

    @property
    def measurement_sets(self) -> bool:
        return any(c.category == Category.MEASUREMENT for c in self.sets)

    @property
    def has_auto_writes(self) -> bool:
        return bool(self.fills or self.appended_layers or self.identity_fills)


def _change(column: str, fv: FieldValue, old: Any, action: str, typer: CellTyper) -> CellChange:
    kind, value = typer(column, fv.value, fv.value_type)
    return CellChange(column=column, old=old, new=value, kind=kind, action=action, raw=fv.raw,
                      policy=fv.policy.value, category=fv.category.value)


def diff_record(rec: SourceRecord, row: dict[str, Any], typer: CellTyper, allow_identity_fill: bool) -> Diff:
    """Compare a matched record with its warehouse row (no writes happen here)."""
    diff = Diff()
    group_compare: dict[str, list[str]] = {}
    for column, fv in rec.fields.items():
        if fv.group and fv.policy == Policy.COMPARE:
            group_compare.setdefault(fv.group, []).append(column)
    for group, columns in group_compare.items():
        if all(is_blank(row.get(c)) for c in columns):
            diff.groups_new.add(group)
    for column, fv in rec.fields.items():
        if fv.value is None or fv.policy in (Policy.PROVENANCE, Policy.DERIVED, Policy.FLAGS):
            continue
        old = row.get(column)
        if fv.policy == Policy.IDENTITY:
            if is_blank(old):
                if allow_identity_fill:
                    diff.identity_fills.append(_change(column, fv, None, "FILL", typer))
            elif not values_equal(column, old, fv.value):
                diff.sets.append(_change(column, fv, old, "SET", typer))
            continue
        if fv.policy == Policy.FILL_ONLY:
            if is_blank(old):
                diff.piggyback.append(_change(column, fv, None, "FILL", typer))
            elif not values_equal(column, old, fv.value):
                diff.fill_only_conflicts.append((column, old, fv.value))
            else:
                diff.unchanged_fields += 1
            continue
        # COMPARE / CORE
        if is_blank(old):
            diff.fills.append(_change(column, fv, None, "FILL", typer))
            if fv.group:
                diff.groups_touched.add(fv.group)
        elif values_equal(column, old, fv.value):
            diff.unchanged_fields += 1
        elif column in JSON_LAYER_COLUMNS and layers_appended(old, fv.value):
            diff.appended_layers.append(_change(column, fv, old, "SET", typer))
            if fv.group:
                diff.groups_touched.add(fv.group)
        else:
            diff.sets.append(_change(column, fv, old, "SET", typer))
            if fv.group:
                diff.groups_touched.add(fv.group)
    return diff


def dependent_cells(rec: SourceRecord, written: set[str], row: dict[str, Any], typer: CellTyper,
                    groups_new: set[str] | None = None) -> list[CellChange]:
    """Derived values follow their raw source; provenance is only *added*, never overwritten.

    Provenance of a period group (``*_source_row``) is written only when the whole
    group is new, so existing values are never re-attributed to another file.
    """
    out: list[CellChange] = []
    groups_new = groups_new or set()
    for column, fv in rec.fields.items():
        if fv.value is None or column in written:
            continue
        old = row.get(column)
        if fv.policy == Policy.DERIVED and fv.derived_from in written:
            if is_blank(old) or not values_equal(column, old, fv.value):
                out.append(_change(column, fv, old, "SET" if not is_blank(old) else "FILL", typer))
        elif fv.policy == Policy.PROVENANCE and is_blank(old) and written:
            if fv.group is None or fv.group in groups_new:
                out.append(_change(column, fv, None, "FILL", typer))
    return out
