#!/usr/bin/env python3
"""Pre-publication privacy scan.

Loads every facility identifier (candidate IDs, AquaWatch IDs, geocodes, GO codes,
bore-log IDs and TW IDs) from a *private* warehouse index and checks every file
that Git would publish for any occurrence. Also blocks spreadsheets and large
files outside the synthetic allow-list. Exit code 1 means: do not push.

    python scripts/privacy_scan.py --index ~/.wash_sync/index/<sha>.v5.sqlite

The scan reads the private index at runtime; no identifiers are stored here.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys

ALLOWED_DATA = ("tests/fixtures/synthetic/", "examples/sources/", "apps/web/public/demo/")
DATA_EXT = (".xlsx", ".xls", ".xlsm", ".csv", ".sqlite", ".db", ".zip", ".psql")
MAX_BYTES = 5 * 1024 * 1024


def tracked_files(root: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root, capture_output=True, text=True,
                         check=True).stdout
    return [line for line in out.splitlines() if line]


def load_identifiers(index_path: str, min_len: int) -> set[str]:
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    ids: set[str] = set()
    for col in ("candidate_id", "aqua_gwc_id", "borelog_id", "borelog_tw_id", "wpms_go_code", "gpani_geocode"):
        for (value,) in conn.execute(f"SELECT DISTINCT {col} FROM rows WHERE {col} IS NOT NULL"):
            value = str(value).strip()
            # Short generic tokens (e.g. 'GWC 1') would match ordinary text; only distinctive ones are checked.
            if len(value) >= min_len and re.search(r"\d{5,}", value):
                ids.add(value)
    conn.close()
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", required=True, help="Private warehouse index (.sqlite)")
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--min-length", type=int, default=8)
    args = parser.parse_args()
    files = tracked_files(args.root)
    problems: list[str] = []
    for f in files:
        full = os.path.join(args.root, f)
        if f.lower().endswith(DATA_EXT) and not f.startswith(ALLOWED_DATA):
            problems.append(f"data file outside the synthetic allow-list: {f}")
        if os.path.isfile(full) and os.path.getsize(full) > MAX_BYTES and not f.endswith("package-lock.json"):
            problems.append(f"large file ({os.path.getsize(full):,} bytes): {f}")
    identifiers = load_identifiers(args.index, args.min_length)
    print(f"Loaded {len(identifiers):,} distinctive real identifiers from the private index")
    by_digits = {re.sub(r"\D", "", i) for i in identifiers if len(re.sub(r"\D", "", i)) >= 10}
    digit_run = re.compile(r"\d{10,}")
    for f in files:
        full = os.path.join(args.root, f)
        if not os.path.isfile(full) or os.path.getsize(full) > 50 * 1024 * 1024:
            continue
        try:
            text = open(full, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for run in set(digit_run.findall(text)):
            if run in by_digits:
                problems.append(f"real identifier digits {run[:4]}… found in {f}")
        for ident in identifiers:
            if not ident.isdigit() and ident in text:
                problems.append(f"real identifier {ident[:6]}… found in {f}")
    print(f"Scanned {len(files):,} publishable files")
    if problems:
        print("PRIVACY SCAN FAILED:")
        for p in sorted(set(problems))[:100]:
            print("  -", p)
        return 1
    print("Privacy scan passed: no real identifiers or private data files found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
