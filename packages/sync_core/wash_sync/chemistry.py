"""Laboratory measurement parsing with explicit, validated unit handling.

Rules
-----
* The raw reading is always preserved verbatim.
* A standardized mg/L value is produced **only** when the unit is stated (or the
  column contract documents it) and the conversion is valid for dilute aqueous
  samples: mg/L (identity), ppm (≡ mg/L), µg/L and ppb (÷ 1000).
* Censored values (``<0.001``, ``<LOQ``, ``BDL``) keep their qualifier and never
  become ``0``. Missing values stay missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .numbers import parse_number
from .text import bangla_to_ascii_digits, collapse_ws, is_placeholder, to_text

ANALYTES = ("arsenic", "iron", "chloride", "manganese")

#: Physically plausible upper bounds (mg/L) used for *flagging*, never for deletion.
PLAUSIBLE_MAX_MG_L = {"arsenic": 5.0, "iron": 100.0, "chloride": 20_000.0, "manganese": 50.0}
SUSPICIOUS_MG_L = {"arsenic": 1.0, "iron": 50.0, "chloride": 5_000.0, "manganese": 10.0}

_UNIT_RULES: list[tuple[re.Pattern[str], str, float | None, str | None]] = [
    # pattern, canonical unit, factor to mg/L, assumption flag
    (re.compile(r"(?:µ|μ|u)g\s*/\s*l\b", re.I), "µg/L", 0.001, "UG_L_CONVERTED_TO_MG_L"),
    (re.compile(r"\bppb\b", re.I), "ppb", 0.001, "PPB_ASSUMED_UG_L_DILUTE_AQUEOUS"),
    (re.compile(r"\bmg\s*/\s*l(?:it(?:er|re))?\b|\bmgl\b|\bmg per l", re.I), "mg/L", 1.0, None),
    (re.compile(r"\bppm\b", re.I), "ppm", 1.0, "PPM_ASSUMED_MG_L_DILUTE_AQUEOUS"),
    (re.compile(r"(?:µ|μ|u)s\s*/\s*cm|-?us/cm", re.I), "µS/cm", None, None),
    (re.compile(r"\bntu\b", re.I), "NTU", None, None),
    (re.compile(r"\bcfu\b", re.I), "CFU", None, None),
]


@dataclass(slots=True)
class Measurement:
    raw: str | None
    value: float | None = None  # numeric value in the stated unit
    unit: str | None = None
    qualifier: str | None = None
    mg_l: float | None = None
    flags: list[str] = field(default_factory=list)
    is_missing: bool = False

    @property
    def missing(self) -> bool:
        """True for blank or placeholder readings (``N/A``); the raw text is still kept."""
        return self.is_missing or self.raw is None

    @property
    def censored(self) -> bool:
        return self.qualifier is not None


def detect_unit(text: str) -> tuple[str | None, float | None, str | None]:
    for pattern, unit, factor, flag in _UNIT_RULES:
        if pattern.search(text):
            return unit, factor, flag
    return None, None, None


def parse_measurement(value: Any, analyte: str, documented_unit: str | None = None) -> Measurement:
    """Parse one laboratory reading.

    ``documented_unit`` is used only when a column contract states the unit (for
    example WPMS readings that carry ``mg/L`` in the report) — never as a guess.
    """
    raw = to_text(value)
    if raw is None or is_placeholder(raw):
        return Measurement(raw=raw, is_missing=True)
    text = collapse_ws(bangla_to_ascii_digits(raw))
    number = parse_number(text)
    m = Measurement(raw=raw, value=number.value, qualifier=number.qualifier)
    unit, factor, assumption = detect_unit(text)
    if unit is None and documented_unit:
        unit, factor, assumption = detect_unit(documented_unit)
        if unit:
            m.flags.append("UNIT_FROM_COLUMN_CONTRACT")
    m.unit = unit
    label = analyte.upper()
    if number.qualifier == "BDL" or (number.qualifier and number.qualifier.startswith("<")):
        m.flags.append(f"{label}_CENSORED_OR_BELOW_LOQ")
    if number.value is None and number.qualifier != "BDL":
        m.flags.append(f"{label}_NON_NUMERIC_OR_UNSUPPORTED")
        return m
    if "DECIMAL_COMMA_CORRECTED" in number.issues:
        m.flags.append(f"{label}_DECIMAL_COMMA_CORRECTED")
    if unit is None:
        m.flags.append(f"{label}_UNSPECIFIED_UNIT")
    elif factor is None:
        m.flags.append(f"{label}_NON_MASS_CONCENTRATION_UNIT")
    elif number.value is not None and not number.qualifier:
        m.mg_l = round(number.value * factor, 9)
        if assumption:
            m.flags.append(f"{label}_{assumption}")
    if number.value is not None:
        check = m.mg_l if m.mg_l is not None else number.value
        if number.value < 0:
            m.flags.append(f"{label}_NEGATIVE_VALUE")
        elif analyte in PLAUSIBLE_MAX_MG_L and m.mg_l is not None:
            if check > PLAUSIBLE_MAX_MG_L[analyte]:
                m.flags.append(f"{label}_IMPLAUSIBLE_VALUE")
            elif check > SUSPICIOUS_MG_L[analyte]:
                m.flags.append(f"{label}_SUSPICIOUS_HIGH_VALUE")
    return m


# ---------------------------------------------------------------------------
# WPMS "Water Quality" free-text field, e.g. "Arsenic: 0.002 mg/L; Iron: 0.5 mg/L"
# ---------------------------------------------------------------------------

#: WPMS parameter name (normalized) → (warehouse column, analyte key for checks)
WPMS_PARAMETERS: dict[str, tuple[str, str]] = {
    "arsenic": ("wpms_arsenic_mg_l", "arsenic"),
    "iron": ("wpms_iron_mg_l", "iron"),
    "chloride": ("wpms_chloride_mg_l", "chloride"),
    "manganese": ("wpms_manganese_mg_l", "manganese"),
    "ph": ("wpms_ph_reported_value", "ph"),
    "tds": ("wpms_tds_mg_l", "tds"),
    "totaldissolvedsolids": ("wpms_tds_mg_l", "tds"),
    "faecalcoliform": ("wpms_faecal_coliform_reported_cfu", "faecal_coliform"),
    "fecalcoliform": ("wpms_faecal_coliform_reported_cfu", "faecal_coliform"),
    "totalcoliform": ("wpms_total_coliform_reported_cfu", "total_coliform"),
    "electricalconductivity": ("wpms_electrical_conductivity_raw", "ec"),
    "ec": ("wpms_electrical_conductivity_raw", "ec"),
    "residualchlorine": ("wpms_residual_chlorine_mg_l", "residual_chlorine"),
    "turbidity": ("wpms_turbidity_ntu", "turbidity"),
    "calcium": ("wpms_calcium_mg_l", "calcium"),
    "iodine": ("wpms_iodine_mg_l", "iodine"),
    "tetrachloroethylene": ("wpms_tetrachloroethylene_mg_l", "pce"),
}


@dataclass(slots=True)
class WpmsWaterQuality:
    raw: str | None
    values: dict[str, Any] = field(default_factory=dict)  # warehouse column -> value
    flags: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)


def parse_wpms_water_quality(value: Any) -> WpmsWaterQuality:
    raw = to_text(value)
    result = WpmsWaterQuality(raw=raw)
    if raw is None or is_placeholder(raw):
        result.flags.append("WPMS_NO_WATER_QUALITY")
        return result
    seen: dict[str, Any] = {}
    for part in re.split(r"[;\n]+", raw):
        part = part.strip()
        if not part:
            continue
        name, sep, reading = part.partition(":")
        if not sep:
            result.unparsed.append(part)
            continue
        key = re.sub(r"[^a-z]", "", name.lower())
        mapping = WPMS_PARAMETERS.get(key)
        if mapping is None:
            result.unparsed.append(part)
            continue
        column, analyte = mapping
        reading = reading.strip()
        if column.endswith("_raw"):
            stored: Any = reading
        else:
            number = parse_number(reading)
            if number.value is None or number.qualifier:
                result.unparsed.append(part)
                continue
            stored = number.value
            if analyte in PLAUSIBLE_MAX_MG_L:
                if number.value < 0:
                    result.flags.append(f"WPMS_{analyte.upper()}_NEGATIVE_VALUE")
                elif number.value > PLAUSIBLE_MAX_MG_L[analyte]:
                    result.flags.append(f"WPMS_{analyte.upper()}_IMPLAUSIBLE_VALUE")
            if analyte == "ph" and re.search(r"mg\s*/\s*l", reading, re.I):
                result.flags.append("WPMS_PH_UNIT_MISLABELLED")
            if analyte in ("faecal_coliform", "total_coliform") and not re.search(r"cfu|/100", reading, re.I):
                result.flags.append("WPMS_COLIFORM_BASIS_UNKNOWN")
        if column in seen and seen[column] != stored:
            result.flags.append("WPMS_REPEATED_PARAMETER_CONFLICT")
            continue
        seen[column] = stored
    result.values = seen
    if result.unparsed:
        result.flags.append("UNPARSED_MEASUREMENT")
    return result
