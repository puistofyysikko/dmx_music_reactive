#!/usr/bin/env bash
# Launch dmxreactive from anywhere: scripts/run.sh <subcommand> [args]
cd "$(dirname "$0")/.." && exec .venv/bin/dmxreactive "$@"
