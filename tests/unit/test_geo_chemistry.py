"""Coordinate validation and laboratory unit handling."""

import pytest

from wash_sync.chemistry import parse_measurement, parse_wpms_water_quality
from wash_sync.geo import haversine_m, resolve_coordinates


def test_valid_coordinates():
    c = resolve_coordinates("23.700001", "90.300002")
    assert c.status == "VALID" and c.usable and c.precision == 6


def test_reversed_coordinates_are_swapped_and_raw_kept():
    c = resolve_coordinates("89.123456", "24.654321")
    assert c.status == "REVERSED_CORRECTED"
    assert (c.lat, c.lon) == (24.654321, 89.123456)
    assert c.lat_raw == "89.123456" and "GPS_REVERSED_CORRECTED" in c.flags
    assert c.correction_reason


@pytest.mark.parametrize("lat,lon,status", [
    ("23", "312", "INVALID"), ("10.5", "45.2", "OUT_OF_RANGE"), ("", "", "MISSING"), ("0", "0", "MISSING"),
    ("1234567890", "9876543210123", "INVALID"), ("23.5", "", "INVALID"),
])
def test_invalid_coordinates(lat, lon, status):
    c = resolve_coordinates(lat, lon)
    assert c.status == status and not c.usable


def test_bangla_digits_and_low_precision():
    c = resolve_coordinates("২৪.১২৩৪৫৬", "৯০.৬৫৪৩২১")
    assert c.usable and c.lat == pytest.approx(24.123456)
    low = resolve_coordinates("23.051", "90.333")
    assert "GPS_LOW_PRECISION_3_DECIMALS" in low.flags


def test_haversine():
    assert haversine_m(23.0, 90.0, 23.0, 90.0) == 0
    assert haversine_m(23.0, 90.0, 23.001, 90.0) == pytest.approx(111.2, abs=0.5)


def test_mg_l_and_ppm_and_ug_l():
    assert parse_measurement("0.05 mg/L", "arsenic").mg_l == 0.05
    ppm = parse_measurement("0.05 ppm", "arsenic")
    assert ppm.mg_l == 0.05 and "ARSENIC_PPM_ASSUMED_MG_L_DILUTE_AQUEOUS" in ppm.flags
    ug = parse_measurement("50 µg/L", "arsenic")
    assert ug.mg_l == pytest.approx(0.05) and "ARSENIC_UG_L_CONVERTED_TO_MG_L" in ug.flags
    ppb = parse_measurement("10 ppb", "arsenic")
    assert ppb.mg_l == pytest.approx(0.01)


def test_missing_unit_is_never_assumed():
    m = parse_measurement("0.545", "iron")
    assert m.value == 0.545 and m.mg_l is None and "IRON_UNSPECIFIED_UNIT" in m.flags


def test_censored_and_missing_readings():
    loq = parse_measurement("<LOQ Mg/l", "arsenic")
    assert loq.mg_l is None and loq.qualifier == "BDL" and "ARSENIC_CENSORED_OR_BELOW_LOQ" in loq.flags
    lt = parse_measurement("<0.001 PPM", "arsenic")
    assert lt.qualifier == "<" and lt.mg_l is None
    missing = parse_measurement("N/A Mg/l", "chloride")
    assert missing.missing and missing.mg_l is None


def test_negative_and_implausible_values_are_flagged_not_dropped():
    neg = parse_measurement("-5 mg/L", "iron")
    assert neg.value == -5 and "IRON_NEGATIVE_VALUE" in neg.flags
    huge = parse_measurement("25000 mg/L", "arsenic")
    assert "ARSENIC_IMPLAUSIBLE_VALUE" in huge.flags


def test_bangla_digit_readings():
    m = parse_measurement("০.০০৬ Mg/l", "arsenic")
    assert m.mg_l == pytest.approx(0.006)


def test_wpms_water_quality_text():
    wq = parse_wpms_water_quality("Arsenic: 0.001 mg/L; Iron: 0.6 mg/L; pH: 7.1 mg/L; Faecal Coliform: 0")
    assert wq.values["wpms_arsenic_mg_l"] == 0.001
    assert wq.values["wpms_iron_mg_l"] == 0.6
    assert wq.values["wpms_ph_reported_value"] == 7.1
    assert "WPMS_PH_UNIT_MISLABELLED" in wq.flags
    assert "WPMS_COLIFORM_BASIS_UNKNOWN" in wq.flags


def test_wpms_repeated_parameter_conflict():
    wq = parse_wpms_water_quality("Arsenic: 0.001 mg/L; Arsenic: 0.05 mg/L")
    assert "WPMS_REPEATED_PARAMETER_CONFLICT" in wq.flags
    assert wq.values["wpms_arsenic_mg_l"] == 0.001
    assert "WPMS_NO_WATER_QUALITY" in parse_wpms_water_quality(None).flags
