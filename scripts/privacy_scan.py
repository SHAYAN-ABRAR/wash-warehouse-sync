#!/usr/bin/env python3
"""Pre-publication privacy scan.

Loads every facility identifier (candidate IDs, AquaWatch IDs, geocodes, GO codes,
bore-log IDs and TW IDs) from a *private* warehouse index and checks every file
that Git would publish for any occurrence. Also blocks spreadsheets and large
files outside the synthetic allow-list. Exit code 1 means: do not push.

    python scripts/privacy_scan.py --index ~/.wash_sync/index/<sha>.v6.sqlite

Matching is exact and linear-time: every real identifier contains a run of at least
nine digits (geocodes, GO codes, TW IDs, AquaWatch and bore-log IDs), so each file's
digit runs are checked against the set of identifier digit runs, and candidate-ID
tokens (``GP:…``, ``WPMS:…``, ``AQUA…:…``, ``BORELOG:…``) are checked verbatim.
No identifiers are stored in this script or in the repository.
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
DIGIT_RUN = re.compile(r"[0-9]{9,}")
CANDIDATE_TOKEN = re.compile(r"(?:GP|WPMS|BORELOG|AQUA[0-9A-Z]*):[^\s\"',;|<>]+")
BANGLA = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def tracked_files(root: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root, capture_output=True, text=True,
                         check=True).stdout
    return [line for line in out.splitlines() if line]


def load_identifiers(index_path: str) -> tuple[set[str], set[str]]:
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    runs: set[str] = set()
    candidates: set[str] = set()
    for col in ("candidate_id", "aqua_gwc_id", "borelog_id", "borelog_tw_id", "wpms_go_code", "gpani_geocode"):
        for (value,) in conn.execute(f"SELECT DISTINCT {col} FROM rows WHERE {col} IS NOT NULL"):
            text = str(value).translate(BANGLA)
            if col == "candidate_id":
                candidates.add(text)
            runs.update(DIGIT_RUN.findall(text))
    conn.close()
    return runs, candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", required=True, help="Private warehouse index (.sqlite)")
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    args = parser.parse_args()
    files = tracked_files(args.root)
    problems: list[str] = []
    for f in files:
        full = os.path.join(args.root, f)
        if f.lower().endswith(DATA_EXT) and not f.startswith(ALLOWED_DATA):
            problems.append(f"data file outside the synthetic allow-list: {f}")
        if os.path.isfile(full) and os.path.getsize(full) > MAX_BYTES and not f.endswith("package-lock.json"):
            problems.append(f"large file ({os.path.getsize(full):,} bytes): {f}")
    runs, candidates = load_identifiers(args.index)
    print(f"Loaded {len(runs):,} identifier digit runs and {len(candidates):,} candidate IDs from the private index")
    scanned = 0
    for f in files:
        full = os.path.join(args.root, f)
        if not os.path.isfile(full):
            continue
        with open(full, "rb") as fh:
            raw = fh.read()
        if f.lower().endswith((".xlsx", ".xlsm")):
            import zipfile  # noqa: PLC0415

            try:
                with zipfile.ZipFile(full) as zf:
                    raw = b"".join(zf.read(n) for n in zf.namelist() if n.endswith(".xml"))
            except zipfile.BadZipFile:
                problems.append(f"unreadable workbook: {f}")
                continue
        text = raw.decode("utf-8", errors="ignore").translate(BANGLA)
        scanned += 1
        for run in set(DIGIT_RUN.findall(text)) & runs:
            problems.append(f"real identifier digits {run[:4]}…({len(run)} digits) found in {f}")
        for token in set(CANDIDATE_TOKEN.findall(text)) & candidates:
            problems.append(f"real candidate ID {token[:8]}… found in {f}")
    print(f"Scanned {scanned:,} publishable files (workbooks unpacked)")
    if problems:
        print("PRIVACY SCAN FAILED:")
        for p in sorted(set(problems))[:100]:
            print("  -", p)
        return 1
    print("Privacy scan passed: no real identifiers or private data files found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
