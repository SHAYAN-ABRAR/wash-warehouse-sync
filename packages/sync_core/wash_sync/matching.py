"""Deterministic, explainable facility matching.

Level 1  exact, trusted source identity (AquaWatch resource ID [+ type for the
         historical GWC table], bore-log ID, WPMS GO code, GPANI geocode+GPS key).
Level 2  explicitly validated crosswalk (reviewed decisions) or a shared government
         code (TW ID / GO code = GPANI geocode) verified by admin, GPS, type and depth.
Level 3  geographic proximity. Never merged automatically: candidates are proposed
         for review. Location alone (same village, same coordinates) is never proof
         of identity.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .adapters.base import normalize_well_type
from .admin import LEVELS, AdminResolver
from .config import Settings
from .geo import haversine_m, in_bangladesh
from .index import WarehouseIndex
from .numbers import FT_TO_M, parse_number
from .plan import Evidence, MatchInfo
from .records import SourceRecord
from .text import norm_code

L1_LOCATION_CONFLICT_M = 5000.0
SOURCE_ID_COLUMN = {"AQUAWATCH": "aqua_gwc_id", "BORELOG": "borelog_id", "WPMS": "wpms_go_code"}
FAMILY_COLUMN = {"AQUAWATCH": "aqua_gwc_id", "BORELOG": "borelog_id", "WPMS": "wpms_go_code", "GPANI": "gpani_source_geocode"}


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    p = parse_number(v)
    return p.value if not p.qualifier else None


def _coord_key(value: Any) -> str:
    from .numbers import strict_number  # noqa: PLC0415

    number = strict_number(value)
    return f"{number:.6f}" if number is not None else "NA"


def row_type_key(row: dict[str, Any]) -> str | None:
    for col in ("aqua_well_type", "borelog_tubewell_type", "facility_type", "wpms_facility_type_raw"):
        key = normalize_well_type(row.get(col))[0]
        if key and key != "OTHER":
            return key
    return None


def row_depth(row: dict[str, Any]) -> tuple[float | None, bool]:
    """(depth, unit_known_as_metres)."""
    for col in ("wpms_total_depth_m", "borelog_well_depth_m", "aqua_well_depth_m"):
        v = _num(row.get(col))
        if v is not None:
            return v, True
    v = _num(row.get("gpani_tubewell_depth_raw"))
    return (v, False) if v is not None else (None, False)


def row_coords(row: dict[str, Any]) -> tuple[float | None, float | None]:
    lat, lon = _num(row.get("latitude")), _num(row.get("longitude"))
    return (lat, lon) if in_bangladesh(lat, lon) else (None, None)


class Matcher:
    def __init__(self, index: WarehouseIndex, settings: Settings, resolver: AdminResolver,
                 crosswalk: list[dict[str, str]] | None = None) -> None:
        self.idx = index
        self.ms = settings.matching
        self.resolver = resolver
        self.links: dict[tuple[str, str], list[str]] = {}
        self.separations: set[tuple[str, str, str]] = set()
        for row in crosswalk or []:
            key = (row.get("source", ""), row.get("source_record_id", ""))
            if row.get("link_type") in ("VERIFIED_AUTO", "APPROVED_MATCH"):
                self.links.setdefault(key, []).append(row.get("candidate_id", ""))
            elif row.get("link_type") == "KEEP_SEPARATE":
                self.separations.add((*key, row.get("candidate_id", "")))
        self._cache: OrderedDict[int, dict[str, Any]] = OrderedDict()

    # ------------------------------------------------------------ row access
    def row(self, num: int) -> dict[str, Any]:
        if num in self._cache:
            self._cache.move_to_end(num)
            return self._cache[num]
        data = self.idx.get_row(num) or {}
        self._cache[num] = data
        if len(self._cache) > 5000:
            self._cache.popitem(last=False)
        return data

    def summary(self, num: int, distance: float | None = None) -> dict[str, Any]:
        r = self.row(num)
        keep = ("candidate_id", "division", "district", "upazila", "union_name", "village_name", "facility_type",
                "latitude", "longitude", "record_sources", "match_status", "aqua_gwc_id", "aqua_well_type",
                "aqua_well_depth_m", "borelog_id", "borelog_tw_id_no", "borelog_well_depth_m", "wpms_go_code",
                "wpms_total_depth_m", "gpani_source_geocode", "gpani_tubewell_depth_raw")
        out = {"row": num, **{k: r.get(k) for k in keep if r.get(k) is not None}}
        if distance is not None:
            out["distance_m"] = round(distance, 1)
        return out

    # ------------------------------------------------------------ evidence
    def compare(self, rec: SourceRecord, row: dict[str, Any]) -> tuple[list[Evidence], dict[str, str]]:
        ev: list[Evidence] = []
        verdict: dict[str, str] = {}
        rlat, rlon = row_coords(row)
        if rec.coords and rec.coords.usable and rlat is not None:
            d = haversine_m(rec.coords.lat, rec.coords.lon, rlat, rlon)
            ok = d <= self.ms.verify_gps_max_m
            ev.append(Evidence(check="gps_distance_m", value=round(d, 1), result="AGREE" if ok else "DISAGREE"))
            verdict["gps"] = "AGREE" if ok else "DISAGREE"
            verdict["gps_m"] = f"{d:.1f}"
        else:
            ev.append(Evidence(check="gps_distance_m", value=None, result="UNKNOWN"))
            verdict["gps"] = "UNKNOWN"
        if rec.admin is not None:
            cmp = self.resolver.compare(rec.admin, {lvl: row.get(lvl) for lvl in LEVELS})
            for lvl, res in cmp.items():
                ev.append(Evidence(check=f"admin_{lvl}", value=f"{rec.admin.resolved.get(lvl)} / {row.get(lvl)}",
                                   result=res))  # type: ignore[arg-type]
            verdict["admin"] = "DISAGREE" if any(cmp[lv] == "DISAGREE" for lv in ("division", "district", "upazila")) else (
                "AGREE" if all(cmp[lv] == "AGREE" for lv in ("division", "district", "upazila")) else "UNKNOWN")
            verdict["district"] = cmp["district"]
            verdict["division"] = cmp["division"]
        rtype = row_type_key(row)
        if rec.well_type and rtype:
            res = "AGREE" if rec.well_type == rtype else "DISAGREE"
            ev.append(Evidence(check="well_type", value=f"{rec.well_type} / {rtype}", result=res))  # type: ignore[arg-type]
            verdict["type"] = res
        else:
            ev.append(Evidence(check="well_type", value=f"{rec.well_type} / {rtype}", result="UNKNOWN"))
            verdict["type"] = "UNKNOWN"
        rdepth, metres = row_depth(row)
        if rec.depth_m is not None and rdepth is not None:
            tol = max(self.ms.depth_tolerance_abs_m, self.ms.depth_tolerance_ratio * max(rec.depth_m, rdepth))
            if abs(rec.depth_m - rdepth) <= tol:
                res, note = "AGREE", "same unit"
            elif not metres and abs(rec.depth_m - rdepth * FT_TO_M) <= tol:
                res, note = "AGREE", "agrees if the existing value is in feet"
            elif not metres and abs(rec.depth_m * FT_TO_M - rdepth) <= tol:
                res, note = "AGREE", "agrees if the incoming value is in feet"
            else:
                res, note = "DISAGREE", "outside tolerance"
            ev.append(Evidence(check="depth", value=f"{rec.depth_m:g} vs {rdepth:g} ({note})", result=res))  # type: ignore[arg-type]
            verdict["depth"] = res
        else:
            ev.append(Evidence(check="depth", value=None, result="UNKNOWN"))
            verdict["depth"] = "UNKNOWN"
        return ev, verdict

    # ------------------------------------------------------------ main
    def match(self, rec: SourceRecord) -> MatchInfo:
        handler = {"AQUAWATCH": self._aquawatch, "BORELOG": self._borelog, "WPMS": self._wpms, "GPANI": self._gpani}
        return handler[rec.source](rec)

    def content_identical(self, rec: SourceRecord, row: dict[str, Any]) -> bool:
        """True when every compared field of the record already holds the same value in ``row``."""
        from .changes import is_blank, values_equal  # noqa: PLC0415
        from .records import Policy  # noqa: PLC0415

        compared = [(c, fv) for c, fv in rec.fields.items() if fv.policy == Policy.COMPARE and fv.value is not None]
        return bool(compared) and all(not is_blank(row.get(c)) and values_equal(c, row.get(c), fv.value)
                                      for c, fv in compared)

    def _by_content(self, rec: SourceRecord, rows: list[int]) -> int | None:
        """Pick the single row whose content equals the record (attributes first, then measurements)."""
        from .changes import is_blank, values_equal  # noqa: PLC0415
        from .records import Policy  # noqa: PLC0415

        def strict(row: dict[str, Any]) -> bool:
            fields = [(c, fv) for c, fv in rec.fields.items() if fv.value is not None
                      and fv.policy in (Policy.COMPARE, Policy.FILL_ONLY, Policy.IDENTITY)]
            return all(not is_blank(row.get(c)) and values_equal(c, row.get(c), fv.value) for c, fv in fields)

        for test in (strict, lambda row: self.content_identical(rec, row)):
            same = [r for r in rows if test(self.row(r))]
            if len(same) == 1:
                return same[0]
        return None

    def _single(self, rec: SourceRecord, row_num: int, level: str, confidence: str, note: str) -> MatchInfo:
        row = self.row(row_num)
        ev, verdict = self.compare(rec, row)
        info = MatchInfo(level=level, confidence=confidence, category="SAME_FACILITY", candidate_rows=[row_num],
                         candidate_ids=[str(row.get("candidate_id"))], candidates=[self.summary(row_num)],
                         evidence=[Evidence(check="identity", value=note, result="MATCH"), *ev], note=note)
        if level == "L1_SOURCE_ID":
            # Spelling variants of admin names are not identity conflicts; only a real
            # location jump (> 5 km by GPS, or a different division without GPS) is.
            far = verdict.get("gps") == "DISAGREE" and float(verdict.get("gps_m", 0)) > L1_LOCATION_CONFLICT_M
            division_conflict = verdict.get("gps") == "UNKNOWN" and verdict.get("division") == "DISAGREE"
            if (far or division_conflict) and not self.content_identical(rec, row):
                info.category = "CONFLICTING_IDENTIFIERS"
                info.conflicts.append("SAME_SOURCE_ID_DIFFERENT_LOCATION")
                info.note = ("Same source ID but the facility location differs by more than 5 km"
                             if far else "Same source ID but a different division (possible ID reuse)")
        return info

    def _crosswalk(self, rec: SourceRecord) -> MatchInfo | None:
        if not rec.source_record_id:
            return None
        for cid in self.links.get((rec.source, rec.source_record_id), []):
            rows = self.idx.rows_where("candidate_id", cid)
            if len(rows) == 1:
                return self._single(rec, rows[0], "L2_CROSSWALK", "VERIFIED", f"reviewed crosswalk link to {cid}")
        return None

    def _aquawatch(self, rec: SourceRecord) -> MatchInfo:
        rid = rec.source_record_id
        rows = self.idx.rows_where("aqua_gwc_id", rid) if rid else []
        historical = bool(rec.identity_hints.get("historical"))
        if rows and rec.well_type and (historical or len(rows) > 1):
            typed = [r for r in rows if normalize_well_type(self.row(r).get("aqua_well_type"))[0] == rec.well_type]
            if typed or historical:
                rows = typed
        if len(rows) > 1 and historical and rec.identity_hints.get("station"):
            lat_key, lon_key = rec.identity_hints["station"]
            same_station = [r for r in rows if _coord_key(self.row(r).get("aqua_latitude_raw")) == lat_key
                            and _coord_key(self.row(r).get("aqua_longitude_raw")) == lon_key]
            if len(same_station) == 1:
                rows = same_station
        if len(rows) == 1:
            note = "AquaWatch resource ID" + (" + well type + station (historical GWC ids are reused)" if historical else "")
            return self._single(rec, rows[0], "L1_SOURCE_ID", "EXACT", note)
        if len(rows) > 1 and (same := self._by_content(rec, rows)) is not None:
            return self._single(rec, same, "L1_SOURCE_ID", "EXACT",
                                "AquaWatch resource ID shared by several rows; this record's content is identical to one of them")
        if len(rows) > 1:
            return MatchInfo(level="L1_SOURCE_ID", confidence="NONE", category="CONFLICTING_IDENTIFIERS",
                             candidate_rows=rows, candidate_ids=[str(self.row(r).get("candidate_id")) for r in rows],
                             candidates=[self.summary(r) for r in rows], conflicts=["SOURCE_ID_ON_MULTIPLE_ROWS"],
                             note=f"Resource ID {rid} appears on {len(rows)} warehouse rows")
        return self._crosswalk(rec) or self._geographic(rec)

    def _borelog(self, rec: SourceRecord) -> MatchInfo:
        bid = rec.source_record_id
        rows = self.idx.rows_where("borelog_id", bid) if bid else []
        if len(rows) == 1:
            return self._single(rec, rows[0], "L1_SOURCE_ID", "EXACT", "bore-log ID")
        if len(rows) > 1:
            return self._conflict(rec, rows, "SOURCE_ID_ON_MULTIPLE_ROWS", f"Bore-log ID {bid} appears on {len(rows)} rows")
        cw = self._crosswalk(rec)
        if cw:
            return cw
        tw = rec.identity_hints.get("tw_code")
        if tw:
            other_bores = [r for r in self.idx.rows_where("borelog_tw_id", tw)]
            if other_bores:
                return self._conflict(rec, other_bores, "UNRESOLVED_DUPLICATE_TW_ID",
                                      f"TW ID {tw} already belongs to another bore log")
            gp = [r for r in self.idx.rows_where("gpani_geocode", tw) if not self.row(r).get("borelog_id")]
            if gp:
                return self._verify_code(rec, gp, "TW ID equals GPANI geocode", "UNRESOLVED_CODE")
        return self._geographic(rec)

    def _wpms(self, rec: SourceRecord) -> MatchInfo:
        go = rec.source_record_id
        rows = self.idx.rows_where("wpms_go_code", go) if go else []
        if len(rows) == 1:
            return self._single(rec, rows[0], "L1_SOURCE_ID", "EXACT", "WPMS GO code")
        if len(rows) > 1:
            return self._conflict(rec, rows, "SOURCE_ID_ON_MULTIPLE_ROWS", f"GO code {go} appears on {len(rows)} rows")
        cw = self._crosswalk(rec)
        if cw:
            return cw
        code = norm_code(go)
        if code:
            gp = [r for r in self.idx.rows_where("gpani_geocode", code) if not self.row(r).get("wpms_go_code")]
            if gp:
                return self._verify_code(rec, gp, "GO code equals GPANI geocode", "EXACT_GO_CODE_UNVERIFIED")
        return self._geographic(rec)

    def _gpani(self, rec: SourceRecord) -> MatchInfo:
        key = rec.source_record_id
        rows = self.idx.rows_where("candidate_id", key) if key else []
        if len(rows) == 1:
            return self._single(rec, rows[0], "L1_SOURCE_ID", "EXACT", "GPANI geocode + source GPS identity")
        cw = self._crosswalk(rec)
        if cw:
            return cw
        geocode = rec.identity_hints.get("geocode")
        same_code = self.idx.rows_where("gpani_geocode", geocode) if geocode else []
        if same_code:
            info = self._conflict(rec, same_code, "GEOCODE_REUSED_OR_GPS_CHANGED",
                                  "The geocode exists with different coordinates — either a GPS correction or a different "
                                  "well sharing a location geocode (geocodes are not unique well identifiers)")
            return info
        return self._geographic(rec)

    def _conflict(self, rec: SourceRecord, rows: list[int], code: str, note: str) -> MatchInfo:
        cands, evidence = [], []
        for r in rows[:10]:
            ev, _ = self.compare(rec, self.row(r))
            cands.append(self.summary(r))
            evidence.extend(Evidence(check=f"row{r}:{e.check}", value=e.value, result=e.result) for e in ev)
        return MatchInfo(level="L1_SOURCE_ID", confidence="NONE", category="CONFLICTING_IDENTIFIERS",
                         candidate_rows=rows[:10], candidate_ids=[str(self.row(r).get("candidate_id")) for r in rows[:10]],
                         candidates=cands, evidence=evidence, conflicts=[code], note=note)

    def _verify_code(self, rec: SourceRecord, rows: list[int], what: str, prefix: str) -> MatchInfo:
        if len(rows) > 1:
            return self._conflict(rec, rows, f"{prefix}_DUPLICATE", f"{what}, but the code is on {len(rows)} rows")
        row_num = rows[0]
        ev, verdict = self.compare(rec, self.row(row_num))
        failures = []
        if verdict.get("gps") == "UNKNOWN":
            failures.append("NO_GPS")
        elif verdict.get("gps") == "DISAGREE":
            failures.append("GPS_CONFLICT")
        if verdict.get("admin") == "DISAGREE":
            failures.append("ADMIN_CONFLICT")
        if verdict.get("type") == "DISAGREE":
            failures.append("TYPE_MISMATCH")
        if verdict.get("depth") == "DISAGREE":
            failures.append("DEPTH_CONFLICT")
        elif verdict.get("depth") == "UNKNOWN" and rec.source == "BORELOG":
            failures.append("DEPTH_UNVERIFIED")
        cand = [self.summary(row_num)]
        if not failures:
            return MatchInfo(level="L2_VERIFIED_CROSS_SOURCE", confidence="VERIFIED", category="SAME_FACILITY",
                             candidate_rows=[row_num], candidate_ids=[str(self.row(row_num).get("candidate_id"))],
                             candidates=cand, evidence=[Evidence(check="shared_code", value=what, result="MATCH"), *ev],
                             note=f"{what}; admin, GPS, type and depth verified")
        return MatchInfo(level="L2_VERIFIED_CROSS_SOURCE", confidence="NONE", category="CONFLICTING_IDENTIFIERS",
                         candidate_rows=[row_num], candidate_ids=[str(self.row(row_num).get("candidate_id"))],
                         candidates=cand, evidence=[Evidence(check="shared_code", value=what, result="MATCH"), *ev],
                         conflicts=[f"{prefix}_{f}" for f in failures],
                         note=f"{what}, but verification failed: {', '.join(failures)}")

    def _geographic(self, rec: SourceRecord) -> MatchInfo:
        c = rec.coords
        if c is None or not c.usable:
            return MatchInfo(level="NONE", confidence="NONE", category="NEW_FACILITY",
                             evidence=[Evidence(check="gps", value="no usable coordinates", result="UNKNOWN")],
                             note="No warehouse row carries this source ID; geographic duplicate check not possible without GPS")
        family = FAMILY_COLUMN[rec.source]
        candidates, evidence = [], []
        for row_num, dist in self.idx.near(c.lat, c.lon, self.ms.possible_match_radius_m):
            row = self.row(row_num)
            if (rec.source, rec.source_record_id or "", str(row.get("candidate_id"))) in self.separations:
                continue
            ev, verdict = self.compare(rec, row)
            if verdict.get("type") == "DISAGREE" or verdict.get("district") == "DISAGREE":
                continue
            same_system = row.get(family) is not None
            if same_system and dist > self.ms.exact_coincidence_m:
                continue
            candidates.append(self.summary(row_num, dist))
            evidence.append(Evidence(check=f"row{row_num}:distance_m", value=round(dist, 1), result="INFO"))
            evidence.extend(Evidence(check=f"row{row_num}:{e.check}", value=e.value, result=e.result)
                            for e in ev if e.check != "gps_distance_m")
        if not candidates:
            return MatchInfo(level="NONE", confidence="NONE", category="NEW_FACILITY",
                             evidence=[Evidence(check="geographic_candidates",
                                                value=f"none within {self.ms.possible_match_radius_m:g} m", result="NO_MATCH")],
                             note="No row carries this source ID and no compatible facility is nearby")
        exact = any(cand["distance_m"] <= self.ms.exact_coincidence_m for cand in candidates)
        return MatchInfo(level="L3_GEOGRAPHIC", confidence="POSSIBLE", category="POSSIBLE_DUPLICATE",
                         candidate_rows=[cand["row"] for cand in candidates],
                         candidate_ids=[str(cand.get("candidate_id")) for cand in candidates],
                         candidates=candidates, evidence=evidence,
                         conflicts=["EXACT_GPS_COINCIDENCE" if exact else "NEARBY_COMPATIBLE_FACILITY"],
                         note=("Location is compatible with existing facilities, but GPS alone is not proof of identity; "
                               "review required"))
