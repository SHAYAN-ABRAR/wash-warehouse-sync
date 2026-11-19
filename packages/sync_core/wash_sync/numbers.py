"""Numeric parsing with explicit handling of the formats found in WASH sources.

Covers Bangla digits, decimal commas, thousands separators, censored laboratory
values (``<0.001``, ``<LOQ``, ``BDL``), unit suffixes for lengths (``750 ft``) and
placeholders. Nothing here ever turns a missing value into ``0``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from .text import bangla_to_ascii_digits, collapse_ws, is_placeholder, to_text

FT_TO_M = 0.3048
INCH_TO_M = 0.0254

_NUM_TOKEN = re.compile(r"[-+]?(?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CENSORED = re.compile(r"^(?:<|less than|below)\s*(?:loq|lod|dl|detection limit|bdl)\b", re.I)
_BDL_WORDS = re.compile(r"^(?:bdl|nd|n\.d\.|not detected|below detection(?: limit)?|trace|<\s*loq|<\s*lod)$", re.I)


@dataclass(slots=True)
class ParsedNumber:
    raw: str | None
    value: float | None = None
    qualifier: str | None = None  # "<", ">", "<=", ">=", "BDL"
    decimal_comma: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def censored(self) -> bool:
        return self.qualifier is not None


def _parse_token(token: str, issues: list[str]) -> tuple[float | None, bool]:
    """Parse one numeric token, resolving comma usage. Returns (value, decimal_comma)."""
    decimal_comma = False
    if "," in token:
        if "." in token:
            token = token.replace(",", "")  # 1,234.5 -> thousands separator
        else:
            head, _, tail = token.rpartition(",")
            if token.count(",") == 1 and len(tail) != 3:
                token = f"{head}.{tail}"  # 12,5 -> decimal comma
                decimal_comma = True
            elif token.count(",") == 1 and len(tail) == 3 and head.lstrip("+-") in {"0", ""}:
                token = f"{head or '0'}.{tail}"  # 0,125 -> decimal comma
                decimal_comma = True
            elif token.count(",") == 1:
                issues.append("AMBIGUOUS_COMMA_SEPARATOR")
                token = token.replace(",", "")
            else:
                token = token.replace(",", "")
    try:
        value = float(token)
    except ValueError:
        return None, decimal_comma
    if math.isnan(value) or math.isinf(value):
        return None, decimal_comma
    return value, decimal_comma


def parse_number(value: Any) -> ParsedNumber:
    """Parse a scalar measurement. Units/text after the number are ignored here."""
    if isinstance(value, bool):
        return ParsedNumber(raw=str(value), issues=["NON_NUMERIC"])
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return ParsedNumber(raw=None)
        return ParsedNumber(raw=to_text(value), value=float(value))
    raw = to_text(value)
    if raw is None or is_placeholder(raw):
        return ParsedNumber(raw=raw)
    text = collapse_ws(bangla_to_ascii_digits(raw)).replace("−", "-")
    issues: list[str] = []
    if _BDL_WORDS.match(text) or _CENSORED.match(text):
        return ParsedNumber(raw=raw, qualifier="BDL", issues=["CENSORED_BELOW_DETECTION"])
    qualifier = None
    m = re.match(r"^\s*(<=|>=|≤|≥|<|>)\s*", text)
    if m:
        qualifier = {"≤": "<=", "≥": ">="}.get(m.group(1), m.group(1))
        text = text[m.end():]
    tokens = _NUM_TOKEN.findall(text)
    if not tokens:
        return ParsedNumber(raw=raw, qualifier=qualifier, issues=["NON_NUMERIC"])
    if len(tokens) > 1 and re.search(r"\d[”\"'’]?\s*[-–]\s*\d", text):
        issues.append("RANGE_VALUE")
        return ParsedNumber(raw=raw, qualifier=qualifier, issues=issues)
    parsed, decimal_comma = _parse_token(tokens[0], issues)
    if decimal_comma:
        issues.append("DECIMAL_COMMA_CORRECTED")
    if parsed is None:
        issues.append("NON_NUMERIC")
    return ParsedNumber(raw=raw, value=parsed, qualifier=qualifier, decimal_comma=decimal_comma, issues=issues)


_STRICT_NUMBER = re.compile(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$")


def strict_number(value: Any) -> float | None:
    """Number only when the *whole* text is a plain number (``"1No data found0488"`` → None)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = to_text(value)
    if text is None:
        return None
    text = bangla_to_ascii_digits(text).strip()
    return float(text) if _STRICT_NUMBER.match(text) else None


def plain_number(value: Any) -> float | None:
    """Value of a clean, uncensored number (``None`` for anything ambiguous)."""
    parsed = parse_number(value)
    blocking = [i for i in parsed.issues if i != "DECIMAL_COMMA_CORRECTED"]
    if parsed.qualifier or blocking:
        return None
    return parsed.value


def canonical_number(value: float | int) -> str:
    """Stable text form for hashing: ``5.50`` → ``5.5``, ``1e-05`` → ``0.00001``."""
    if isinstance(value, bool):
        raise TypeError("booleans are not numbers here")
    try:
        dec = Decimal(repr(float(value))).normalize()
    except InvalidOperation:  # pragma: no cover - defensive
        return repr(value)
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def excel_number_text(value: float | int) -> str:
    """Text written into ``<v>`` for numeric cells (shortest round-trip repr)."""
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and abs(value) < 1e15):
        return str(int(value))
    return repr(float(value))


@dataclass(slots=True)
class Length:
    raw: str | None
    meters: float | None = None
    unit: str | None = None  # "m", "ft", "inch", "mm" or None (unstated)
    value: float | None = None  # in the stated unit
    issues: list[str] = field(default_factory=list)


_UNIT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:\bmm\b|millimet(?:er|re)s?)", re.I), "mm"),
    (re.compile(r"(?:\bft\b|\bfeet\b|\bfoot\b|'|’|ফুট|ফিট)", re.I), "ft"),
    (re.compile(r"(?:\binch(?:es)?\b|\bin\b|\"|”|ইঞ্চি)", re.I), "inch"),
    (re.compile(r"(?:\bm\b|\bmeters?\b|\bmetres?\b|\bmtr?s?\b|মিটার)", re.I), "m"),
]


def parse_length(value: Any, default_unit: str | None = None) -> Length:
    """Parse depths/lengths such as ``"750 ft"``, ``"46.02 m"``, ``"৬৬০ ft"``.

    When no unit is stated the value is kept but ``meters`` is only computed if a
    ``default_unit`` is explicitly supplied by the caller (never assumed here).
    """
    raw = to_text(value)
    if raw is None or is_placeholder(raw):
        return Length(raw=raw)
    number = parse_number(raw)
    issues = list(number.issues)
    if number.value is None:
        return Length(raw=raw, issues=issues or ["NON_NUMERIC"])
    text = bangla_to_ascii_digits(raw)
    unit = None
    for pattern, name in _UNIT_PATTERNS:
        if pattern.search(text):
            unit = name
            break
    if unit is None and default_unit:
        unit = default_unit
        issues.append("UNIT_ASSUMED_" + default_unit.upper())
    if unit is None:
        return Length(raw=raw, value=number.value, issues=issues + ["UNIT_UNSTATED"])
    factor = {"m": 1.0, "ft": FT_TO_M, "inch": INCH_TO_M, "mm": 0.001}[unit]
    meters = round(number.value * factor, 6)
    return Length(raw=raw, meters=meters, unit=unit, value=number.value, issues=issues)
