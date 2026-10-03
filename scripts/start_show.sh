#!/usr/bin/env bash
# One-click show start: pre-flight checks, then the live show with the status panel.
# Usage: scripts/start_show.sh [extra `dmxreactive run` options, e.g. --scene ambient]
set -u
cd "$(dirname "$0")/.."
BIN=.venv/bin/dmxreactive
if [[ ! -x "$BIN" ]]; then
    echo "No virtual environment yet: run scripts/setup_dev.sh first."; read -rp "Press Enter..." _; exit 1
fi
if [[ ! -f config/config.yaml ]]; then
    echo "No config/config.yaml yet: copying the example (check the node IP and the patch!)."
    cp config/config.example.yaml config/config.yaml
fi
"$BIN" preflight
status=$?
if [[ $status -ne 0 ]]; then
    read -rp $'\nPre-flight found problems. Start the show anyway? [y/N] ' answer
    [[ "$answer" == [yY]* ]] || exit 1
fi
echo
exec "$BIN" run "$@"
