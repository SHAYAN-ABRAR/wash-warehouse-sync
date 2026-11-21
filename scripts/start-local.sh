#!/usr/bin/env bash
# One-command launch of Local Production Mode (Linux / macOS).
# First run: creates .venv, installs the engine and builds the web interface.
# Then: starts the engine + interface on http://127.0.0.1:8765 (private, this computer only).
#   ./scripts/start-local.sh                 # default state dir ~/.wash_sync
#   ./scripts/start-local.sh --state-dir /secure/wash_sync_state
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON:-python3}"
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  echo "Python 3.12+ is required (set PYTHON=/path/to/python3.12)." >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment (.venv)…"
  "$PYTHON_BIN" -m venv .venv
fi
if ! .venv/bin/python -c 'import wash_sync, fastapi' 2>/dev/null; then
  echo "Installing the engine…"
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e ".[server]"
fi
if [ ! -f apps/web/dist-local/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "Building the local web interface (one time)…"
    npm --prefix apps/web ci --no-audit --no-fund
    npm --prefix apps/web run build:local
  else
    echo "npm not found — the engine will start, and the CLI works, but the web interface is not built." >&2
  fi
fi
exec .venv/bin/python -m wash_sync serve "$@"
