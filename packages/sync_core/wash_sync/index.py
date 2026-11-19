"""Private, rebuildable lookup index for one warehouse version.

The published warehouse stays an ``.xlsx`` file. To avoid rescanning 600k+ rows for
every lookup, one streaming pass builds a SQLite cache keyed by the workbook's
SHA-256. Deleting the cache is always safe — it is rebuilt from the workbook.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from . import __version__
from .admin import LEVELS, AliasTable
from .geo import bbox_deg, haversine_m, in_bangladesh
from .schema import WarehouseSchema, parse_period_column
from .text import norm_code, norm_id, norm_key, split_tokens, to_text
from .xlsx.reader import SheetStream, load_shared_strings, read_workbook_info

INDEX_FORMAT = 5
SRC_BITS = {"GPANI": 1, "WPMS": 2, "AQUAWATCH": 4, "BORELOG": 8}
_COLS_RE = re.compile(rb'<col\b([^>]*?)/>')


def file_sha256(path: str, chunk: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _source_mask(cells: dict[str, Any]) -> int:
    mask = 0
    if cells.get("gpani_source_geocode") is not None or cells.get("gpani_source_row_count") is not None:
        mask |= 1
    if cells.get("wpms_go_code") is not None:
        mask |= 2
    if cells.get("aqua_gwc_id") is not None or cells.get("aqua_record_key") is not None:
        mask |= 4
    if cells.get("borelog_id") is not None:
        mask |= 8
    return mask


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


#: Columns screened for implausible values in the data-quality profile.
OUTLIER_RULES: dict[str, tuple[float | None, float | None, str]] = {
    "wpms_total_depth_m": (0, 600, "m"),
    "aqua_well_depth_m": (0, 600, "m"),
    "borelog_well_depth_m": (0, 600, "m"),
    "borelog_drilling_depth_m": (0, 700, "m"),
    "wpms_arsenic_mg_l": (0, 5, "mg/L"),
    "wpms_iron_mg_l": (0, 100, "mg/L"),
    "wpms_chloride_mg_l": (0, 20000, "mg/L"),
    "borelog_wq_arsenic_mg_l": (0, 5, "mg/L"),
}
WATER_TABLE_MAX_M = 100.0


@dataclass
class AnchorResult:
    after_row: int
    level: str
    explanation: str


class WarehouseIndex:
    """Read API over the SQLite cache. Open one instance per thread."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        meta = dict(self.conn.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("complete") != "1":
            raise RuntimeError(f"Index {db_path} is incomplete; rebuild it")
        self.meta = meta
        self.headers: list[str] = json.loads(meta["headers"])
        self.sha256: str = meta["sha256"]
        self.n_rows: int = int(meta["n_rows"])
        self.sheet_name: str = meta["sheet_name"]
        self._col_index = {h: i + 1 for i, h in enumerate(self.headers)}

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, workbook_path: str, db_path: str, sha256: str | None = None,
              progress: Callable[[float], None] | None = None,
              cancelled: Callable[[], bool] | None = None) -> "WarehouseIndex":
        sha256 = sha256 or file_sha256(workbook_path)
        tmp = db_path + ".building"
        if os.path.exists(tmp):
            os.remove(tmp)
        conn = sqlite3.connect(tmp)
        conn.executescript(
            """
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE rows(
                row_num INTEGER PRIMARY KEY, candidate_id TEXT,
                division TEXT, district TEXT, upazila TEXT, union_name TEXT, village_name TEXT,
                k_div TEXT, k_dist TEXT, k_upa TEXT, k_uni TEXT, k_vil TEXT,
                facility_type TEXT, lat REAL, lon REAL, gps_ok INTEGER,
                record_sources TEXT, match_status TEXT, quality_flags TEXT,
                aqua_gwc_id TEXT, aqua_well_type TEXT, borelog_id TEXT, borelog_tw_id TEXT,
                wpms_go_code TEXT, gpani_geocode TEXT, src_mask INTEGER, search TEXT, cells TEXT);
            CREATE TABLE columns(idx INTEGER PRIMARY KEY, name TEXT, header_style TEXT, nonempty INTEGER,
                n_num INTEGER, n_str INTEGER, dom_style TEXT, max_len INTEGER, num_min REAL, num_max REAL);
            CREATE TABLE bangla_alias(level TEXT, source_key TEXT, target TEXT, support INTEGER, share REAL,
                PRIMARY KEY(level, source_key));
            CREATE TABLE display(level TEXT, key TEXT, name TEXT, support INTEGER, PRIMARY KEY(level, key));
            """
        )
        info = read_workbook_info(workbook_path)
        if not info.sheets:
            raise ValueError("Workbook has no worksheets")
        sheet = info.sheets[0]
        import zipfile  # noqa: PLC0415

        with zipfile.ZipFile(workbook_path) as zf:
            shared = load_shared_strings(zf, info.shared_strings_member)
        stream = SheetStream(workbook_path, sheet.member, progress)
        headers: list[str] = []
        header_styles: dict[str, str | None] = {}
        stats: dict[int, dict[str, Any]] = {}
        row_attrs: collections.Counter[bytes] = collections.Counter()
        display_votes: dict[str, collections.Counter[tuple[str, str]]] = {lvl: collections.Counter() for lvl in LEVELS}
        bangla_votes: dict[str, dict[str, collections.Counter[str]]] = {lvl: {} for lvl in LEVELS[:3]}
        quality = _QualityAccumulator()
        batch: list[tuple[Any, ...]] = []
        insert_sql = "INSERT INTO rows VALUES (" + ",".join("?" * 28) + ")"
        count = 0
        for row in stream.rows():
            cells = row.cells(shared)
            if row.num == 1:
                for c in cells:
                    name = str(c.value)
                    headers.append(name)
                    header_styles[name] = c.style.decode() if c.style else None
                if len(set(headers)) != len(headers):
                    raise ValueError("Duplicate header names in warehouse")
                continue
            count += 1
            if cancelled is not None and count % 5000 == 0 and cancelled():
                conn.close()
                os.remove(tmp)
                raise RuntimeError("Index build cancelled")
            row_attrs[re.sub(rb'\s*\b(r|spans)="[^"]*"', b"", row.attrs)] += 1
            named: dict[str, Any] = {}
            packed: dict[str, Any] = {}
            for c in cells:
                if c.value is None or c.col > len(headers):
                    continue
                name = headers[c.col - 1]
                named[name] = c.value
                packed[str(c.col)] = c.value
                st = stats.get(c.col)
                if st is None:
                    st = stats[c.col] = {"n": 0, "num": 0, "str": 0, "styles": collections.Counter(),
                                         "maxlen": 0, "min": None, "max": None}
                st["n"] += 1
                st["styles"][c.style] += 1
                if c.type == "n" and isinstance(c.value, (int, float)):
                    st["num"] += 1
                    v = float(c.value)
                    st["min"] = v if st["min"] is None or v < st["min"] else st["min"]
                    st["max"] = v if st["max"] is None or v > st["max"] else st["max"]
                else:
                    st["str"] += 1
                    if isinstance(c.value, str) and len(c.value) > st["maxlen"]:
                        st["maxlen"] = len(c.value)
            admin = [to_text(named.get(level)) for level in LEVELS]
            keys = [norm_key(a) for a in admin]
            for level, value, key in zip(LEVELS, admin, keys):
                if value:
                    display_votes[level][(key, value)] += 1
            if "wpms_admin_names_raw" in named:
                _learn_bangla(named["wpms_admin_names_raw"], admin, bangla_votes)
            lat, lon = _num(named.get("latitude")), _num(named.get("longitude"))
            gps_ok = 1 if in_bangladesh(lat, lon) else 0
            quality.add(named, admin, lat, lon, gps_ok)
            search = " | ".join(str(x) for x in (
                named.get("candidate_id"), *admin, named.get("facility_type"), named.get("aqua_gwc_id"),
                named.get("borelog_id"), named.get("wpms_go_code"), named.get("gpani_source_geocode"),
            ) if x is not None).lower()
            batch.append((
                row.num, to_text(named.get("candidate_id")), *admin, *keys,
                to_text(named.get("facility_type")), lat if gps_ok else None, lon if gps_ok else None, gps_ok,
                to_text(named.get("record_sources")), to_text(named.get("match_status")),
                to_text(named.get("quality_flags")),
                norm_id(named.get("aqua_gwc_id")), norm_key(named.get("aqua_well_type")) or None,
                norm_id(named.get("borelog_id")), norm_code(named.get("borelog_tw_id_no")),
                norm_id(named.get("wpms_go_code")), norm_code(named.get("gpani_source_geocode")),
                _source_mask(named), search, json.dumps(packed, ensure_ascii=False, separators=(",", ":")),
            ))
            if len(batch) >= 5000:
                conn.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            conn.executemany(insert_sql, batch)
        if not headers:
            raise ValueError("Warehouse has no header row")
        widths = _parse_widths(stream.prologue)
        col_rows = []
        for idx, name in enumerate(headers, start=1):
            st = stats.get(idx, {"n": 0, "num": 0, "str": 0, "styles": collections.Counter(), "maxlen": 0,
                                 "min": None, "max": None})
            dom_style = st["styles"].most_common(1)[0][0] if st["styles"] else None
            col_rows.append((idx, name, header_styles.get(name), st["n"], st["num"], st["str"],
                             dom_style.decode() if dom_style else None, st["maxlen"], st["min"], st["max"]))
        conn.executemany("INSERT INTO columns VALUES (?,?,?,?,?,?,?,?,?,?)", col_rows)
        for level, votes in display_votes.items():
            best: dict[str, tuple[str, int]] = {}
            totals: collections.Counter[str] = collections.Counter()
            for (key, value), n in votes.items():
                totals[key] += n
                if key not in best or n > best[key][1]:
                    best[key] = (value, n)
            conn.executemany("INSERT INTO display VALUES (?,?,?,?)",
                             [(level, k, v, totals[k]) for k, (v, _n) in best.items()])
        alias_rows = []
        for level, mapping in bangla_votes.items():
            for source_key, counter in mapping.items():
                total = sum(counter.values())
                target, n = counter.most_common(1)[0]
                share = n / total
                if n >= 3 and share >= 0.9:
                    alias_rows.append((level, source_key, target, n, share))
        conn.executemany("INSERT INTO bangla_alias VALUES (?,?,?,?,?)", alias_rows)
        conn.executescript(
            """
            CREATE INDEX ix_cid ON rows(candidate_id);
            CREATE INDEX ix_gwc ON rows(aqua_gwc_id) WHERE aqua_gwc_id IS NOT NULL;
            CREATE INDEX ix_bl ON rows(borelog_id) WHERE borelog_id IS NOT NULL;
            CREATE INDEX ix_bltw ON rows(borelog_tw_id) WHERE borelog_tw_id IS NOT NULL;
            CREATE INDEX ix_go ON rows(wpms_go_code) WHERE wpms_go_code IS NOT NULL;
            CREATE INDEX ix_geo ON rows(gpani_geocode) WHERE gpani_geocode IS NOT NULL;
            CREATE INDEX ix_ll ON rows(lat, lon) WHERE lat IS NOT NULL;
            CREATE INDEX ix_admin ON rows(k_div, k_dist, k_upa, k_uni, k_vil, row_num);
            """
        )
        dominant_attrs = row_attrs.most_common(1)[0][0].decode() if row_attrs else ""
        dims = re.search(rb'<dimension ref="([^"]+)"', stream.prologue)
        autofilter = re.search(rb'<autoFilter ref="([^"]+)"', stream.epilogue)
        q = quality.finish(conn)
        meta = {
            "format": str(INDEX_FORMAT), "engine_version": __version__, "sha256": sha256,
            "workbook_path": os.path.abspath(workbook_path), "workbook_size": str(os.path.getsize(workbook_path)),
            "sheet_name": sheet.name, "sheet_member": sheet.member, "sheet_count": str(len(info.sheets)),
            "headers": json.dumps(headers, ensure_ascii=False), "n_rows": str(count),
            "header_styles": json.dumps(header_styles), "widths": json.dumps(widths),
            "row_attrs": dominant_attrs, "dimension": dims.group(1).decode() if dims else "",
            "autofilter": autofilter.group(1).decode() if autofilter else "",
            "defined_names": json.dumps(info.defined_names, ensure_ascii=False),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "quality": json.dumps(q, ensure_ascii=False),
            "complete": "1",
        }
        conn.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
        conn.commit()
        conn.close()
        os.replace(tmp, db_path)
        if progress:
            progress(1.0)
        return cls(db_path)

    @classmethod
    def open_or_build(cls, workbook_path: str, cache_dir: str, sha256: str | None = None,
                      progress: Callable[[float], None] | None = None,
                      cancelled: Callable[[], bool] | None = None) -> "WarehouseIndex":
        os.makedirs(cache_dir, exist_ok=True)
        sha256 = sha256 or file_sha256(workbook_path)
        db_path = os.path.join(cache_dir, f"{sha256[:24]}.v{INDEX_FORMAT}.sqlite")
        if os.path.exists(db_path):
            try:
                index = cls(db_path)
                if index.sha256 == sha256:
                    return index
                index.close()
            except (RuntimeError, sqlite3.DatabaseError, KeyError):
                pass
            os.remove(db_path)
        index = cls.build(workbook_path, db_path, sha256, progress, cancelled)
        for name in os.listdir(cache_dir):  # drop caches of older index formats for this workbook
            if name.startswith(sha256[:24]) and name.endswith(".sqlite") and name != os.path.basename(db_path):
                try:
                    os.remove(os.path.join(cache_dir, name))
                except OSError:
                    pass
        return index

    # ----------------------------------------------------------------- schema
    def schema(self) -> WarehouseSchema:
        types: dict[str, str] = {}
        styles: dict[str, str | None] = {}
        for r in self.conn.execute("SELECT name, n_num, n_str, dom_style FROM columns"):
            types[r["name"]] = "n" if r["n_num"] > r["n_str"] else "s"
            styles[r["name"]] = r["dom_style"]
        widths = {int(k): v for k, v in json.loads(self.meta.get("widths", "{}")).items()}
        return WarehouseSchema(
            headers=list(self.headers),
            header_styles=json.loads(self.meta.get("header_styles", "{}")),
            column_types=types,
            column_styles=styles,
            widths=widths,
        )

    def column_profiles(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM columns ORDER BY idx")]

    def display_names(self) -> dict[str, dict[str, tuple[str, int]]]:
        out: dict[str, dict[str, tuple[str, int]]] = {}
        for r in self.conn.execute("SELECT level, key, name, support FROM display"):
            out.setdefault(r["level"], {})[r["key"]] = (r["name"], r["support"])
        return out

    def bangla_aliases(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for r in self.conn.execute("SELECT level, source_key, target FROM bangla_alias"):
            out.setdefault(r["level"], {})[r["source_key"]] = r["target"]
        return out

    # ---------------------------------------------------------------- lookups
    def _decode(self, cells_json: str) -> dict[str, Any]:
        packed = json.loads(cells_json)
        return {self.headers[int(k) - 1]: v for k, v in packed.items()}

    def get_row(self, row_num: int) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT cells FROM rows WHERE row_num=?", (row_num,)).fetchone()
        return self._decode(r["cells"]) if r else None

    def get_rows(self, row_nums: Iterable[int]) -> dict[int, dict[str, Any]]:
        nums = sorted(set(row_nums))
        out: dict[int, dict[str, Any]] = {}
        for i in range(0, len(nums), 500):
            chunk = nums[i:i + 500]
            q = f"SELECT row_num, cells FROM rows WHERE row_num IN ({','.join('?' * len(chunk))})"
            for r in self.conn.execute(q, chunk):
                out[r["row_num"]] = self._decode(r["cells"])
        return out

    def rows_where(self, column: str, value: str | None) -> list[int]:
        if value is None:
            return []
        allowed = {"candidate_id", "aqua_gwc_id", "borelog_id", "borelog_tw_id", "wpms_go_code", "gpani_geocode"}
        if column not in allowed:
            raise ValueError(column)
        return [r[0] for r in self.conn.execute(f"SELECT row_num FROM rows WHERE {column}=? ORDER BY row_num", (value,))]

    def near(self, lat: float, lon: float, radius_m: float, limit: int = 25) -> list[tuple[int, float]]:
        dlat, dlon = bbox_deg(lat, radius_m)
        found = []
        for r in self.conn.execute(
            "SELECT row_num, lat, lon FROM rows WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
        ):
            d = haversine_m(lat, lon, r["lat"], r["lon"])
            if d <= radius_m:
                found.append((r["row_num"], d))
        found.sort(key=lambda x: (x[1], x[0]))
        return found[:limit]

    def insertion_anchor(self, admin: dict[str, Any], aliases: AliasTable) -> AnchorResult:
        """Where a new facility row belongs so that rows stay geographically grouped.

        Strategy: find the deepest administrative level whose group already exists,
        then place the row in name order within that group (end of the group for an
        exact village match). Explained in the audit for every inserted row.
        """
        cols = ("k_div", "k_dist", "k_upa", "k_uni", "k_vil")
        keys = [norm_key(admin.get(level)) for level in LEVELS]
        variants = [_variants(aliases, level, key) for level, key in zip(LEVELS, keys)]

        def where(depth: int) -> tuple[str, list[str]]:
            clauses, params = [], []
            for i in range(depth):
                vs = variants[i]
                clauses.append(f"{cols[i]} IN ({','.join('?' * len(vs))})")
                params.extend(vs)
            return (" AND ".join(clauses) or "1=1"), params

        w, p = where(5)
        r = self.conn.execute(f"SELECT MAX(row_num) FROM rows WHERE {w}", p).fetchone()[0]
        if r:
            return AnchorResult(r, "village", "appended at the end of its existing village group")
        for depth in (4, 3, 2, 1):
            w, p = where(depth)
            first = self.conn.execute(f"SELECT MIN(row_num) FROM rows WHERE {w}", p).fetchone()[0]
            if not first:
                continue
            nxt = cols[depth]
            before = self.conn.execute(
                f"SELECT MAX(row_num) FROM rows WHERE {w} AND {nxt} < ?", p + [keys[depth]]
            ).fetchone()[0]
            level = LEVELS[depth - 1]
            label = admin.get(level) or "(blank)"
            if before:
                return AnchorResult(before, level, f"placed in name order within existing {level} group {label!r}")
            return AnchorResult(first - 1, level, f"placed first within existing {level} group {label!r}")
        before = self.conn.execute("SELECT MAX(row_num) FROM rows WHERE k_div < ?", (keys[0],)).fetchone()[0]
        if before:
            return AnchorResult(before, "none", "new division; placed in division name order")
        return AnchorResult(1, "none", "placed directly below the header (no earlier division)")

    # ---------------------------------------------------------------- explorer
    def query(self, q: str | None = None, filters: dict[str, Any] | None = None, columns: list[str] | None = None,
              offset: int = 0, limit: int = 50) -> dict[str, Any]:
        filters = filters or {}
        clauses: list[str] = []
        params: list[Any] = []
        if q:
            clauses.append("search LIKE ?")
            params.append(f"%{q.lower()}%")
        for level, col in (("division", "k_div"), ("district", "k_dist"), ("upazila", "k_upa")):
            if filters.get(level):
                clauses.append(f"{col} = ?")
                params.append(norm_key(filters[level]))
        if filters.get("source") in SRC_BITS:
            clauses.append("(src_mask & ?) != 0")
            params.append(SRC_BITS[filters["source"]])
        if filters.get("match_status"):
            clauses.append("match_status = ?")
            params.append(filters["match_status"])
        if filters.get("flag"):
            clauses.append("quality_flags LIKE ?")
            params.append(f"%{filters['flag']}%")
        if filters.get("gps") == "invalid":
            clauses.append("gps_ok = 0")
        elif filters.get("gps") == "valid":
            clauses.append("gps_ok = 1")
        where = " AND ".join(clauses) or "1=1"
        total = self.conn.execute(f"SELECT COUNT(*) FROM rows WHERE {where}", params).fetchone()[0]
        limit = max(1, min(500, limit))
        rows = self.conn.execute(
            f"SELECT row_num, cells FROM rows WHERE {where} ORDER BY row_num LIMIT ? OFFSET ?",
            params + [limit, max(0, offset)],
        ).fetchall()
        cols = columns or ["candidate_id", "division", "district", "upazila", "union_name", "village_name",
                           "facility_type", "latitude", "longitude", "record_sources", "match_status"]
        cols = [c for c in cols if c in self._col_index]
        items = []
        for r in rows:
            data = self._decode(r["cells"])
            items.append({"row": r["row_num"], **{c: data.get(c) for c in cols}})
        return {"total": total, "offset": offset, "limit": limit, "columns": cols, "items": items}

    def facets(self) -> dict[str, Any]:
        def top(col: str, n: int = 60) -> list[tuple[str, int]]:
            return [(r[0], r[1]) for r in self.conn.execute(
                f"SELECT {col}, COUNT(*) c FROM rows WHERE {col} IS NOT NULL GROUP BY {col} ORDER BY c DESC LIMIT ?", (n,))]
        return {"division": top("division", 20), "match_status": top("match_status", 40)}

    def quality(self) -> dict[str, Any]:
        return json.loads(self.meta.get("quality", "{}"))

    def periods(self) -> list[str]:
        out = []
        for name in self.headers:
            parsed = parse_period_column(name)
            if parsed and parsed[0].key not in out:
                out.append(parsed[0].key)
        return out


def _variants(aliases: AliasTable, level: str, key: str) -> list[str]:
    canonical = aliases.by_level.get(level, {}).get(key, key)
    group = [k for k, v in aliases.by_level.get(level, {}).items() if v == canonical]
    return sorted(set(group + [key]))


def _parse_widths(prologue: bytes) -> dict[int, float]:
    widths: dict[int, float] = {}
    for m in _COLS_RE.finditer(prologue):
        attrs = m.group(1)
        lo = re.search(rb'\bmin="(\d+)"', attrs)
        hi = re.search(rb'\bmax="(\d+)"', attrs)
        w = re.search(rb'\bwidth="([\d.]+)"', attrs)
        if lo and hi and w:
            for i in range(int(lo.group(1)), int(hi.group(1)) + 1):
                widths[i] = float(w.group(1))
    return widths


def _learn_bangla(raw_json: Any, admin: list[str | None], votes: dict[str, dict[str, collections.Counter[str]]]) -> None:
    try:
        data = json.loads(str(raw_json))
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    for level, key in zip(LEVELS[:3], ("Division", "District", "Upazila")):
        source = data.get(key)
        target = admin[LEVELS.index(level)]
        if source and target:
            votes[level].setdefault(norm_key(source), collections.Counter())[target] += 1


class _QualityAccumulator:
    """Collects data-quality statistics during the single index pass."""

    def __init__(self) -> None:
        self.rows = 0
        self.gps = collections.Counter()
        self.missing_admin = collections.Counter()
        self.flags = collections.Counter()
        self.sources = collections.Counter()
        self.match_status = collections.Counter()
        self.outliers = collections.Counter()
        self.water_table_outliers = 0
        self.unit_issues = 0
        self.conflict_flags = 0
        self.missing_type = 0
        self.examples: dict[str, list[str]] = collections.defaultdict(list)

    def add(self, named: dict[str, Any], admin: list[str | None], lat: float | None, lon: float | None, ok: int) -> None:
        self.rows += 1
        if lat is None and lon is None:
            self.gps["missing"] += 1
        elif ok:
            self.gps["valid"] += 1
        elif lat is not None and lon is not None and in_bangladesh(lon, lat):
            self.gps["reversed"] += 1
        else:
            self.gps["invalid"] += 1
        for level, value in zip(LEVELS, admin):
            if not value:
                self.missing_admin[level] += 1
        if not named.get("facility_type"):
            self.missing_type += 1
        flags = split_tokens(named.get("quality_flags"))
        for flag in flags:
            self.flags[flag] += 1
            if "UNIT" in flag:
                self.unit_issues += 1
            if "CONFLICT" in flag:
                self.conflict_flags += 1
        for token in split_tokens(named.get("record_sources")):
            self.sources[token.split("_")[0] if token.startswith("AQUA") else token] += 1
        self.match_status[to_text(named.get("match_status")) or ""] += 1
        for col, (lo, hi, _unit) in OUTLIER_RULES.items():
            v = _num(named.get(col)) if col in named else None
            if v is not None and ((lo is not None and v < lo) or (hi is not None and v > hi)):
                self.outliers[col] += 1
                if len(self.examples[col]) < 5:
                    self.examples[col].append(str(named.get("candidate_id")))
        for name, value in named.items():
            if name.endswith("_water_table_m"):
                v = _num(value)
                if v is not None and (v < 0 or v > WATER_TABLE_MAX_M):
                    self.water_table_outliers += 1

    def finish(self, conn: sqlite3.Connection) -> dict[str, Any]:
        dupes = {}
        for col in ("aqua_gwc_id", "gpani_geocode", "wpms_go_code", "borelog_id", "candidate_id"):
            dupes[col] = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT {col} FROM rows WHERE {col} IS NOT NULL GROUP BY {col} HAVING COUNT(*)>1)"
            ).fetchone()[0]
        exact_gps = conn.execute(
            "SELECT COUNT(*) FROM (SELECT lat, lon FROM rows WHERE lat IS NOT NULL GROUP BY lat, lon HAVING COUNT(*)>1)"
        ).fetchone()[0]
        return {
            "rows": self.rows,
            "gps": dict(self.gps),
            "missing_admin": dict(self.missing_admin),
            "missing_facility_type": self.missing_type,
            "top_flags": self.flags.most_common(40),
            "sources": dict(self.sources),
            "match_status": self.match_status.most_common(30),
            "outliers": {k: {"count": v, "rule": OUTLIER_RULES[k], "examples": self.examples[k]}
                         for k, v in self.outliers.items()},
            "water_table_outliers": self.water_table_outliers,
            "unit_issue_flags": self.unit_issues,
            "conflict_flags": self.conflict_flags,
            "duplicate_identifiers": dupes,
            "shared_exact_coordinates": exact_gps,
        }
