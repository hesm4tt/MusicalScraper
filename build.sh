#!/usr/bin/env bash
# Build the desktop app for THIS platform. Run on macOS for the .app,
# on Windows for the .exe - PyInstaller cannot cross-compile.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ ! -x .venv-build/bin/python ]; then
  "$PY" -m venv .venv-build
  ./.venv-build/bin/python -m pip install --upgrade pip
  ./.venv-build/bin/python -m pip install -r requirements.txt -r requirements-build.txt
fi
./.venv-build/bin/python -m PyInstaller --noconfirm --clean MusicalScraper.spec
echo
echo "Built into ./dist:"
ls -1 dist
