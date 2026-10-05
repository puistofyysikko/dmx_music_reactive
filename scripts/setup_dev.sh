#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
echo "Ready. Try: .venv/bin/dmxreactive check-config"
