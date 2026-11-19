"""Canonical in-memory representation of one incoming source record."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .admin import AdminLocation
from .geo import Coordinates
from .numbers import canonical_number
from .schema import Period
from .text import collapse_ws


class Policy(StrEnum):
    """How a mapped field may change an existing warehouse row."""

    IDENTITY = "identity"  # source identity; only written when a row is created/enriched
    COMPARE = "compare"  # written when blank; a different existing value requires review
    FILL_ONLY = "fill_only"  # written when blank; differences are reported, never applied
    DERIVED = "derived"  # computed from a raw field; follows its source field
    PROVENANCE = "provenance"  # source row/file references; never a change by itself
    FLAGS = "flags"  # token list; tokens are appended, never removed
    CORE = "core"  # core facility columns; set on insert, filled when blank


class Category(StrEnum):
    MEASUREMENT = "measurement"
    METADATA = "metadata"


class ChangeClass(StrEnum):
    NEW_FACILITY = "NEW_FACILITY"
    NEW_OBSERVATION = "NEW_OBSERVATION"
    NEW_SOURCE_METADATA = "NEW_SOURCE_METADATA"
    UPDATED_MEASUREMENT = "UPDATED_MEASUREMENT"
    FILLED_MISSING_VALUE = "FILLED_MISSING_VALUE"
    UNCHANGED = "UNCHANGED"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    POTENTIAL_DUPLICATE = "POTENTIAL_DUPLICATE"
    CONFLICTING_IDENTITY = "CONFLICTING_IDENTITY"
    CONFLICTING_MEASUREMENT = "CONFLICTING_MEASUREMENT"
    INVALID_SOURCE_RECORD = "INVALID_SOURCE_RECORD"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


#: Classes whose operations may be applied without an item-level decision (the
#: plan as a whole must still be approved or explicitly authorized).
AUTO_APPLICABLE = {
    ChangeClass.NEW_FACILITY,
    ChangeClass.NEW_OBSERVATION,
    ChangeClass.FILLED_MISSING_VALUE,
}
REVIEW_CLASSES = {
    ChangeClass.UPDATED_MEASUREMENT,
    ChangeClass.NEW_SOURCE_METADATA,
    ChangeClass.POTENTIAL_DUPLICATE,
    ChangeClass.CONFLICTING_IDENTITY,
    ChangeClass.CONFLICTING_MEASUREMENT,
    ChangeClass.REQUIRES_REVIEW,
}


@dataclass(slots=True)
class Issue:
    code: str
    severity: str  # ERROR | WARNING | INFO
    message: str
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message, "field": self.field}


@dataclass(slots=True)
class FieldValue:
    value: Any
    policy: Policy
    category: Category = Category.METADATA
    raw: str | None = None
    group: str | None = None
    derived_from: str | None = None
    value_type: str = "auto"  # number | string | auto (follow the column's convention)


@dataclass
class SourceRecord:
    source: str
    format: str
    source_file: str
    source_sheet: str
    source_row: int
    source_record_id: str | None
    observation_key: str
    fields: dict[str, FieldValue] = field(default_factory=dict)
    core: dict[str, Any] = field(default_factory=dict)
    period: Period | None = None
    admin: AdminLocation | None = None
    coords: Coordinates | None = None
    well_type: str | None = None
    depth_m: float | None = None
    issues: list[Issue] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    preview: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    identity_hints: dict[str, Any] = field(default_factory=dict)

    def issue(self, code: str, severity: str, message: str, field_name: str | None = None) -> None:
        if not any(i.code == code and i.field == field_name for i in self.issues):
            self.issues.append(Issue(code, severity, message, field_name))

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)

    def add_flag(self, *flags: str) -> None:
        for f in flags:
            if f and f not in self.flags:
                self.flags.append(f)


def canonical_value(value: Any) -> str:
    """Deterministic text used for equality and hashing.

    Numbers compare numerically (``5.50`` == ``5.5``); text is whitespace-normalized
    and case-folded. Raw representations are still preserved elsewhere.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return canonical_number(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    text = collapse_ws(str(value))
    try:
        if text and text.lstrip("+-").replace(".", "", 1).isdigit():
            return canonical_number(float(text))
    except ValueError:
        pass
    return text.casefold()


def compute_fingerprint(record: SourceRecord) -> str:
    """SHA-256 of the record's identity and compared content (not provenance)."""
    payload = {
        "source": record.source,
        "key": record.observation_key,
        "fields": {
            name: canonical_value(fv.value)
            for name, fv in sorted(record.fields.items())
            if fv.policy not in (Policy.PROVENANCE, Policy.FLAGS)
        },
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
