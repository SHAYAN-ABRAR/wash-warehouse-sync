"""Privacy guard: everything shipped publicly (demo bundle, examples) must be synthetic."""

from __future__ import annotations

import json
import os
import re

import pytest
from wash_sync.adapters.base import AdapterContext, default_resolver, detect_source, read_records

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEMO = os.path.join(ROOT, "apps", "web", "public", "demo")
SYNTHETIC_CANDIDATE = re.compile(r"^(GP:99\d{21}@|WPMS:98\d{21}$|AQUA[0-9A-Z]*:SYN|AQUA:SYN|BORELOG:SYN)")
SYNTHETIC_SOURCE_ID = re.compile(r"^(SYN|GP:99\d{21}@|98\d{21}$|NO GROUND WATER RESOURCE$)")


@pytest.mark.skipif(not os.path.isdir(DEMO), reason="demo bundle not generated")
def test_demo_bundle_is_flagged_and_entirely_synthetic():
    with open(os.path.join(DEMO, "manifest.json"), encoding="utf-8") as fh:
        assert json.load(fh)["synthetic"] is True
    with open(os.path.join(DEMO, "warehouse.json"), encoding="utf-8") as fh:
        bundle = json.load(fh)
    assert bundle["rows"], "demo warehouse is empty"
    for row in bundle["rows"]:
        values = row["values"]
        assert SYNTHETIC_CANDIDATE.match(values["candidate_id"]), values["candidate_id"]
        for col, prefix in (("aqua_gwc_id", "SYN"), ("borelog_id", "SYNBL"), ("wpms_go_code", "98"), ("gpani_source_geocode", "99")):
            if col in values:
                assert str(values[col]).startswith(prefix), (col, values[col])


@pytest.mark.parametrize("folder", [os.path.join(DEMO, "samples"), os.path.join(ROOT, "examples", "sources")])
def test_sample_exports_contain_only_synthetic_identifiers(folder):
    if not os.path.isdir(folder):
        pytest.skip("no samples")
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        det = detect_source(path)
        assert det.source, name
        for rec in read_records(path, AdapterContext(default_resolver(), name, path), det):
            if rec.source_record_id:
                assert SYNTHETIC_SOURCE_ID.match(rec.source_record_id), (name, rec.source_record_id)
