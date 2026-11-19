"""Text normalization primitives shared by every adapter.

Normalization is used for *comparison and indexing only*. Original source values
are always preserved separately (``*_raw`` columns, audit records).
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_WS = re.compile(r"\s+")
# Removes punctuation/spacing only. Bangla vowel signs and viramas are combining marks
# (not ``\w``); they are kept so that distinct Bangla names never collapse together.
_NON_ALNUM = re.compile(r"[^\w\u0980-\u09FF]+|_", re.UNICODE)
_FLOAT_INT = re.compile(r"^(-?\d+)\.0+$")

#: Tokens that represent "no value" in the source systems. They are never treated as
#: real measurements and are never converted to zero.
PLACEHOLDER_TOKENS = frozenset(
    {
        "", "-", "--", "---", ".", "?", "n/a", "na", "n.a", "n.a.", "none", "null", "nil",
        "no data", "nodata", "not available", "not found", "unknown", "nan", "#n/a",
        "n/a mg/l", "na mg/l", "- mg/l", "none mg/l",
    }
)


def to_text(value: Any) -> str | None:
    """Convert a cell value to stripped text, mapping empty/NaN to ``None``.

    Integral floats coming from legacy ``.xls`` readers (``12.0``) are rendered
    without the spurious decimal so identifiers survive the round trip.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    if isinstance(value, int):
        return str(value)
    text = str(value).replace(" ", " ").strip()
    return text or None


def collapse_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def bangla_to_ascii_digits(text: str) -> str:
    return text.translate(BANGLA_DIGITS)


def has_bangla(text: str | None) -> bool:
    return bool(text) and any("ঀ" <= ch <= "৿" for ch in text)  # type: ignore[union-attr]


def is_placeholder(value: Any) -> bool:
    text = to_text(value)
    if text is None:
        return True
    return collapse_ws(text).lower() in PLACEHOLDER_TOKENS


def clean(value: Any) -> str | None:
    """Return normalized display text, or ``None`` for empty/placeholder values."""
    text = to_text(value)
    if text is None:
        return None
    text = collapse_ws(unicodedata.normalize("NFC", text))
    if text.lower() in PLACEHOLDER_TOKENS:
        return None
    return text


def norm_key(value: Any) -> str:
    """Comparison key: upper-case, Bangla digits → ASCII, punctuation/space removed.

    ``"Barisal Sadar (Kotwali)"`` and ``"BARISAL SADAR KOTWALI"`` share a key, but no
    fuzzy matching is performed — different names stay different.
    """
    text = clean(value)
    if text is None:
        return ""
    text = bangla_to_ascii_digits(unicodedata.normalize("NFKC", text)).upper()
    return _NON_ALNUM.sub("", text)


def norm_id(value: Any) -> str | None:
    """Normalize a source-issued identifier without changing its meaning.

    Upper-cases letters, converts Bangla digits, collapses whitespace and strips a
    float artefact suffix (``"123456789012.0"`` → ``"123456789012"``).
    """
    text = to_text(value)
    if text is None:
        return None
    text = collapse_ws(bangla_to_ascii_digits(unicodedata.normalize("NFKC", text))).upper()
    match = _FLOAT_INT.match(text)
    if match:
        text = match.group(1)
    if text.lower() in PLACEHOLDER_TOKENS:
        return None
    return text or None


def norm_code(value: Any) -> str | None:
    """Digits-only form of government geocodes / GO codes / TW ID numbers."""
    text = to_text(value)
    if text is None:
        return None
    digits = re.sub(r"\D", "", bangla_to_ascii_digits(text))
    return digits or None


def norm_header(value: Any) -> str:
    """Header comparison key used for header-based (not position-based) mapping."""
    text = to_text(value) or ""
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^0-9a-zঀ-৿]+", "", text)


def split_tokens(value: Any) -> list[str]:
    """Split a ``;``/``|`` separated token list (quality flags, record sources)."""
    text = to_text(value)
    if not text:
        return []
    return [t.strip() for t in re.split(r"[;|]", text) if t.strip()]


def join_tokens(existing: Any, new_tokens: list[str]) -> tuple[str | None, list[str]]:
    """Append tokens that are not yet present, keeping the existing separator.

    Returns the new value and the list of tokens actually added. Re-applying the
    same tokens is a no-op, which keeps repeated synchronizations idempotent.
    """
    text = to_text(existing) or ""
    sep = "|" if ("|" in text and ";" not in text) else ";"
    current = split_tokens(text)
    added = [t for t in dict.fromkeys(new_tokens) if t and t not in current]
    if not added:
        return (text or None), []
    merged = sep.join(current + added) if current else sep.join(added)
    return merged, added
