#!/usr/bin/env bash
# Start the local BAEC Console on synthetic data.
#
#   ./scripts/start_demo.sh            install into .venv on first run, then start in the background
#   ./scripts/stop_demo.sh             stop it
#
# Development mode only: local SQLite files under .demo/data, served on 127.0.0.1. Nothing here touches AWS,
# PostgreSQL, email, a calendar, or any external CRM, and no buyer is contacted. AI Assist is off until you enter
# your own Claude API key in the browser sidebar; this script never reads or passes one.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${BAEC_DEMO_PORT:-8501}"
STATE="$ROOT/.demo"
PID_FILE="$STATE/streamlit.pid"
LOG_FILE="$STATE/streamlit.log"
URL="http://localhost:$PORT"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "BAEC demo is already running: $URL  (stop it with ./scripts/stop_demo.sh)"
  exit 0
fi

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  echo "BAEC needs Python 3.12 or newer. Set PYTHON=/path/to/python3.12+ and run again." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv ..."
  "$PYTHON" -m venv .venv
fi
STAMP=".venv/.baec-demo-requirements"
if ! cmp -s requirements.txt "$STAMP" 2>/dev/null; then
  echo "Installing requirements.txt into .venv ..."
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
  cp requirements.txt "$STAMP"
fi

if ! .venv/bin/python -c 'import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)' "$PORT"; then
  echo "Port $PORT is already in use by another program. Choose another port, for example:" >&2
  echo "    BAEC_DEMO_PORT=8502 ./scripts/start_demo.sh" >&2
  exit 1
fi

mkdir -p "$STATE/data"

# Development mode is explicit, and development mode reads no database or sign-in setting. Any Anthropic credential
# in this shell is removed from the demo's environment as well, so a key can only come from the browser session.
(
  cd baec_console
  exec env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
    BAEC_DEPLOYMENT_MODE=development BAEC_CONSOLE_DATA_DIR="$STATE/data" \
    nohup ../.venv/bin/python -m streamlit run app.py \
      --server.address 127.0.0.1 --server.port "$PORT" --server.headless true \
      --browser.gatherUsageStats false
) >"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

for _ in $(seq 1 60); do
  if kill -0 "$(cat "$PID_FILE")" 2>/dev/null && curl -fsS "http://127.0.0.1:$PORT/_stcore/health" >/dev/null 2>&1; then
    echo
    echo "BAEC demo is running (synthetic data, development mode):"
    echo
    echo "    $URL"
    echo
    echo "In the sidebar, choose \"Load demo: start from call notes\" and follow docs/DEMO_WALKTHROUGH.md."
    echo "Stop it with ./scripts/stop_demo.sh. Log: .demo/streamlit.log"
    exit 0
  fi
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "The demo did not start. Last lines of .demo/streamlit.log:" >&2
tail -n 20 "$LOG_FILE" >&2 || true
kill "$(cat "$PID_FILE")" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
