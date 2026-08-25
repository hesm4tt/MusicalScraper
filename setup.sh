#!/usr/bin/env bash
# One-time setup. Creates a local venv - nothing is installed system-wide.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
echo
echo "Done. Run it with:   ./musical-scraper @username --dry-run"
command -v ffmpeg >/dev/null || echo "Optional: 'brew install ffmpeg' (not required for downloading)"
