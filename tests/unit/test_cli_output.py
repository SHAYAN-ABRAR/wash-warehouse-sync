"""The CLI must not crash when its output goes to a legacy code page (Windows pipes, CI logs)."""

from __future__ import annotations

import io
import os
import subprocess
import sys

from wash_sync import cli

SAMPLE = "v000 → v001 · 5 µg/L · বরিশাল"


def test_legacy_code_page_streams_switch_to_utf8(monkeypatch):
    raw = io.BytesIO()
    legacy = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", legacy)
    cli._ensure_unicode_output()
    print(SAMPLE)
    legacy.flush()
    monkeypatch.undo()
    assert raw.getvalue().decode("utf-8").rstrip() == SAMPLE


def test_cli_help_survives_redirected_output_on_a_legacy_code_page():
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    proc = subprocess.run([sys.executable, "-m", "wash_sync", "--help"], capture_output=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    assert "preview" in proc.stdout.decode("utf-8")
