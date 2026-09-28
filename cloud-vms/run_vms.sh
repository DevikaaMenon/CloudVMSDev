#!/usr/bin/env bash
# =====================================================================
#  Gatehouse Cloud VMS - launcher for Linux / macOS   (./run_vms.sh)
#   1. checks every prerequisite, installs only what is missing
#   2. starts the server (log: data/server.log)
#   3. waits until it answers, then opens the dashboard in a new Chrome tab
#  Press Ctrl+C in this terminal to stop the system.
# =====================================================================
set -uo pipefail
cd "$(dirname "$0")"
PORT=${VMS_PORT:-8000}
URL="http://localhost:$PORT/"
HEALTH="http://127.0.0.1:$PORT/api/health"

bash ./install_prerequisites.sh || { echo "Setup did not finish. Fix the problem above and run ./run_vms.sh again."; exit 1; }

is_up() {
  if command -v curl >/dev/null 2>&1; then curl -s -f -o /dev/null --max-time 5 "$HEALTH"
  else .venv/bin/python -c "import urllib.request,sys; urllib.request.urlopen('$HEALTH', timeout=5)" 2>/dev/null; fi
}

open_browser() {
  echo "Opening $URL in Google Chrome ..."
  if [ "$(uname -s)" = "Darwin" ]; then
    for app in "Google Chrome" "Chromium"; do open -a "$app" "$URL" 2>/dev/null && return 0; done
    open "$URL"; return 0
  fi
  for c in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$c" >/dev/null 2>&1; then
      nohup "$c" "$URL" >/dev/null 2>&1 &   # adds a tab to a running Chrome, or starts it
      return 0
    fi
  done
  echo "Chrome not found - opening your default browser instead."
  xdg-open "$URL" >/dev/null 2>&1 || echo "Open $URL in your browser."
}

if is_up; then
  echo "The server is already running."
  open_browser
  exit 0
fi

mkdir -p data
echo "Starting the server (log: data/server.log) ..."
( cd backend && exec ../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" ) >data/server.log 2>&1 &
SERVER=$!
trap 'echo; echo "Stopping the server ..."; kill $SERVER 2>/dev/null; wait $SERVER 2>/dev/null; exit 0' INT TERM

echo "Waiting for it to be ready (the first start can take a minute or two) ..."
for _ in $(seq 1 150); do
  if is_up; then break; fi
  if ! kill -0 $SERVER 2>/dev/null; then
    echo "The server stopped. Last lines of data/server.log:"; tail -n 20 data/server.log; exit 1
  fi
  sleep 2
done
if ! is_up; then echo "The server did not answer within 5 minutes - see data/server.log"; kill $SERVER; exit 1; fi

echo "Server is up."
if [ -f data/initial_admin_password.txt ]; then
  echo; echo "---- first sign-in ----"; cat data/initial_admin_password.txt; echo "-----------------------"
fi
open_browser
echo
echo "Dashboard: $URL"
echo "The system runs while this terminal is open. Press Ctrl+C to stop it."
wait $SERVER
