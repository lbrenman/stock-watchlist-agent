#!/usr/bin/env bash
# Start the web app (port 8000, static files from web/) and the A2A agent (port 9000), skipping whichever is already running.
# Safe to run any number of times. Used by the Codespaces start and attach hooks.
#
#   bash scripts/start.sh             start what is not running
#   bash scripts/start.sh --restart   stop both, then start both (use after editing .env)
#
# The web app is only files: it has no server logic and no settings. It calls the agent straight from the browser.
# Logs: /tmp/web.log (web app) and /tmp/a2a.log (A2A agent).
cd "$(dirname "$0")/.." || exit 0

case "${1:-}" in
  ""|--restart) ;;
  *) echo "Unknown option: $1"; echo "Usage: bash scripts/start.sh [--restart]"; exit 2 ;;
esac

UI_PORT="${UI_PORT:-8000}"
A2A_PORT="${A2A_PORT:-9000}"
WAIT_SECS="${WAIT_SECS:-25}"

# If a start is already in progress (start hook and attach hook can overlap), let it finish.
exec 9>/tmp/swa-start.lock
flock -n 9 || { echo "Another start is in progress; nothing to do."; exit 0; }

if [ "${1:-}" = "--restart" ]; then
  pkill -f "http.server ${UI_PORT}" 2>/dev/null
  pkill -f "app.a2a_server" 2>/dev/null
  sleep 1
fi

up() { curl -fsS -m 2 -o /dev/null "$1" 2>/dev/null; }
UI_URL="http://127.0.0.1:${UI_PORT}/"
A2A_URL="http://127.0.0.1:${A2A_PORT}/.well-known/agent-card.json"

if up "$UI_URL"; then
  echo "web app:   already running on :${UI_PORT}"
else
  # 9>&- closes the lock descriptor for the child, or the long-running server would hold the lock forever
  nohup python -m http.server "$UI_PORT" --bind 0.0.0.0 --directory web > /tmp/web.log 2>&1 9>&- &
  disown 2>/dev/null
  STARTED_UI=1
fi

if up "$A2A_URL"; then
  echo "A2A agent: already running on :${A2A_PORT}"
else
  nohup python -m app.a2a_server > /tmp/a2a.log 2>&1 9>&- &
  disown 2>/dev/null
  STARTED_A2A=1
fi

wait_for() {  # name url logfile
  local i=0
  while [ "$i" -lt "$WAIT_SECS" ]; do
    up "$2" && { echo "$1 is up"; return 0; }
    sleep 1; i=$((i + 1))
  done
  echo "$1 FAILED to start within ${WAIT_SECS}s. Last lines of $3:"
  tail -n 15 "$3" 2>/dev/null | sed 's/^/    /'
  return 1
}

[ -n "${STARTED_UI:-}" ]  && { wait_for "web app:   " "$UI_URL" /tmp/web.log; }
[ -n "${STARTED_A2A:-}" ] && { wait_for "A2A agent: " "$A2A_URL" /tmp/a2a.log; }

echo "Logs: tail -f /tmp/a2a.log (agent)   tail -f /tmp/web.log (web app)"

# Never fail the Codespace lifecycle hook; the messages above say what went wrong.
exit 0
