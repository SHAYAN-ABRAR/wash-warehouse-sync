"""Coordinate parsing and validation for Bangladesh facility locations.

Raw coordinates are never discarded: callers keep the raw strings and store the
corrected values together with an explicit correction reason.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .text import bangla_to_ascii_digits, is_placeholder, to_text

# Approximate national bounding box (padded slightly beyond 20.6–26.6 N, 88.0–92.7 E).
BD_LAT = (20.3, 26.9)
BD_LON = (87.9, 92.9)
EARTH_RADIUS_M = 6_371_008.8


def parse_coordinate(value: Any) -> tuple[float | None, int]:
    """Parse one coordinate. Returns ``(value, decimals)``; decimals = precision."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None, 0
        text = repr(float(value))
    else:
        text = to_text(value)
        if text is None or is_placeholder(text):
            return None, 0
        text = bangla_to_ascii_digits(text).strip().replace("°", "").strip()
        text = re.sub(r"\s*[NnEe]$", "", text)
        if text.count(",") == 1 and "." not in text:
            text = text.replace(",", ".")
    match = re.fullmatch(r"[-+]?\d+(?:\.(\d+))?", text.strip())
    if not match:
        try:
            num = float(text)
        except ValueError:
            return None, 0
        return (None, 0) if math.isnan(num) else (num, 0)
    decimals = len(match.group(1) or "")
    return float(text), decimals


def in_bangladesh(lat: float | None, lon: float | None) -> bool:
    return (
        lat is not None
        and lon is not None
        and BD_LAT[0] <= lat <= BD_LAT[1]
        and BD_LON[0] <= lon <= BD_LON[1]
    )


@dataclass(slots=True)
class Coordinates:
    lat_raw: str | None
    lon_raw: str | None
    lat: float | None = None
    lon: float | None = None
    status: str = "MISSING"  # VALID | REVERSED_CORRECTED | MISSING | OUT_OF_RANGE | INVALID
    correction_reason: str | None = None
    precision: int = 0  # min decimals of the two components
    flags: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status in ("VALID", "REVERSED_CORRECTED")


def resolve_coordinates(lat_value: Any, lon_value: Any) -> Coordinates:
    """Validate a raw lat/lon pair, detecting reversal and out-of-range values."""
    lat_raw, lon_raw = to_text(lat_value), to_text(lon_value)
    lat, lat_dec = parse_coordinate(lat_value)
    lon, lon_dec = parse_coordinate(lon_value)
    coords = Coordinates(lat_raw=lat_raw, lon_raw=lon_raw)
    if lat is None and lon is None:
        coords.flags.append("GPS_MISSING")
        return coords
    if lat is None or lon is None:
        coords.status = "INVALID"
        coords.flags.append("GPS_INCOMPLETE")
        return coords
    if lat == 0 and lon == 0:
        coords.status = "MISSING"
        coords.flags.append("GPS_ZERO_PLACEHOLDER")
        return coords
    coords.precision = min(lat_dec, lon_dec)
    if in_bangladesh(lat, lon):
        coords.lat, coords.lon, coords.status = lat, lon, "VALID"
    elif in_bangladesh(lon, lat):
        coords.lat, coords.lon, coords.status = lon, lat, "REVERSED_CORRECTED"
        coords.correction_reason = "latitude/longitude were reversed in the source; swapped"
        coords.flags.append("GPS_REVERSED_CORRECTED")
    elif -90 <= lat <= 90 and -180 <= lon <= 180:
        coords.status = "OUT_OF_RANGE"
        coords.flags.append("GPS_OUTSIDE_BANGLADESH")
    else:
        coords.status = "INVALID"
        coords.flags.append("GPS_INVALID_RANGE")
    if coords.usable and coords.precision and coords.precision < 4:
        coords.flags.append(f"GPS_LOW_PRECISION_{coords.precision}_DECIMALS")
    return coords


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bbox_deg(lat: float, radius_m: float) -> tuple[float, float]:
    """Half-widths (dlat, dlon) in degrees of a box containing a radius around ``lat``."""
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    return dlat, dlon
