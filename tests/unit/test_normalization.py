"""Text, identifier, number, depth and date-format normalization."""

import pytest

from wash_sync.numbers import canonical_number, parse_length, parse_number, plain_number
from wash_sync.text import clean, is_placeholder, join_tokens, norm_code, norm_header, norm_id, norm_key, to_text


def test_bangla_digits_and_ids():
    assert norm_id("  ১২৩৪৫৬৭৮৯০১২ ") == "123456789012"
    assert norm_id("123456789012.0") == "123456789012"
    assert norm_id("syn-1234") == "SYN-1234"
    assert norm_id("GWC   1") == "GWC 1"
    assert norm_id("N/A") is None
    assert norm_code("২০৯৯-১-০১-২৩-৪৫") == "20991012345"


def test_norm_key_is_punctuation_insensitive_but_not_fuzzy():
    assert norm_key("Barisal Sadar (Kotwali)") == norm_key("BARISAL SADAR KOTWALI")
    assert norm_key("Kala Para") == norm_key("KALAPARA")
    assert norm_key("Pirganj") != norm_key("Pirgacha")  # similar names stay different


def test_placeholders_never_become_values():
    for token in ("", "N/A", "none", "No data", "-", "nan"):
        assert is_placeholder(token)
        assert clean(token) is None
    assert parse_number("N/A").value is None
    assert parse_number("").value is None


def test_to_text_drops_float_artifacts():
    assert to_text(12.0) == "12"
    assert to_text(0.1) == "0.1"
    assert to_text(float("nan")) is None


@pytest.mark.parametrize("raw,expected", [
    ("5.50", 5.5), ("12,5", 12.5), ("0,125", 0.125), ("1,234.5", 1234.5), ("৬৬০", 660.0), ("−3", -3.0), ("1e-3", 0.001),
])
def test_parse_number_formats(raw, expected):
    assert parse_number(raw).value == pytest.approx(expected)


def test_decimal_comma_flagged_and_ambiguous_comma_reported():
    assert "DECIMAL_COMMA_CORRECTED" in parse_number("12,5").issues
    assert "AMBIGUOUS_COMMA_SEPARATOR" in parse_number("1,234").issues
    assert plain_number("1,234") is None  # ambiguous: not silently accepted


def test_censored_values_keep_qualifier_and_never_zero():
    p = parse_number("<0.001")
    assert p.qualifier == "<" and p.value == 0.001
    for raw in ("<LOQ", "BDL", "ND", "below detection limit"):
        q = parse_number(raw)
        assert q.qualifier == "BDL" and q.value is None


def test_ranges_are_not_collapsed_to_one_number():
    assert "RANGE_VALUE" in parse_number("42.26-45.26 m").issues
    assert parse_number("42.26-45.26 m").value is None


def test_canonical_number_equivalence():
    assert canonical_number(5.50) == canonical_number(5.5) == "5.5"
    assert canonical_number(1e-05) == "0.00001"
    assert canonical_number(-0.0) == "0"


@pytest.mark.parametrize("raw,meters,unit", [
    ("750 ft", 228.6, "ft"), ("46.02 m", 46.02, "m"), ("৬৬০ ft", 201.168, "ft"), ("250'", 76.2, "ft"),
    ("100 mm", 0.1, "mm"), ("6 inch", 0.1524, "inch"), ("248 মিটার", 248.0, "m"),
])
def test_parse_length_units(raw, meters, unit):
    length = parse_length(raw)
    assert length.unit == unit
    assert length.meters == pytest.approx(meters)


def test_parse_length_never_assumes_units():
    length = parse_length("230")
    assert length.meters is None and length.value == 230 and "UNIT_UNSTATED" in length.issues
    assumed = parse_length("230", default_unit="m")
    assert assumed.meters == 230 and "UNIT_ASSUMED_M" in assumed.issues


def test_decimal_comma_depth():
    length = parse_length("12,5 m")
    assert length.meters == 12.5 and "DECIMAL_COMMA_CORRECTED" in length.issues


def test_header_normalization():
    assert norm_header("Arsenic (AS)") == norm_header("arsenic (As)")
    assert norm_header("Water Table (m)") == "watertablem"


def test_join_tokens_idempotent_and_separator_preserving():
    value, added = join_tokens("AQUA_2025;AQUA_2026_WET", ["AQUA_2027_DRY"])
    assert value == "AQUA_2025;AQUA_2026_WET;AQUA_2027_DRY" and added == ["AQUA_2027_DRY"]
    again, added2 = join_tokens(value, ["AQUA_2027_DRY"])
    assert again == value and added2 == []
    piped, _ = join_tokens("GPANI|WPMS", ["BORELOG"])
    assert piped == "GPANI|WPMS|BORELOG"
