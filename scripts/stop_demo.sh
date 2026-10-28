#!/usr/bin/env bash
# Stop the local BAEC demo started by ./scripts/start_demo.sh.
#
#   ./scripts/stop_demo.sh             stop the server; synthetic data in .demo/data is kept
#   ./scripts/stop_demo.sh --reset     stop the server and delete .demo/ (all local demo data)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.demo/streamlit.pid"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in $(seq 1 10); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 1
    done
    echo "BAEC demo stopped."
  else
    echo "BAEC demo was not running."
  fi
  rm -f "$PID_FILE"
else
  echo "BAEC demo was not running."
fi

if [ "${1:-}" = "--reset" ]; then
  rm -rf "$ROOT/.demo"
  echo "Local demo data deleted."
fi
