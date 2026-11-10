#!/usr/bin/env bash
# Start the chat UI (port 8000) and the A2A agent (port 9000), skipping whichever is already running.
# Safe to run any number of times. Used by the Codespaces start and attach hooks.
#
#   bash scripts/start.sh             start what is not running
#   bash scripts/start.sh --restart   stop both, then start both (use after editing .env)
#
# Logs: /tmp/uvicorn.log (UI) and /tmp/a2a.log (A2A agent).
cd "$(dirname "$0")/.." || exit 0

UI_PORT="${UI_PORT:-8000}"
A2A_PORT="${A2A_PORT:-9000}"
WAIT_SECS="${WAIT_SECS:-25}"

# If a start is already in progress (start hook and attach hook can overlap), let it finish.
exec 9>/tmp/swa-start.lock
flock -n 9 || { echo "Another start is in progress; nothing to do."; exit 0; }

if [ "${1:-}" = "--restart" ]; then
  pkill -f "uvicorn app.server" 2>/dev/null
  pkill -f "app.a2a_server" 2>/dev/null
  sleep 1
fi

up() { curl -fsS -m 2 -o /dev/null "$1" 2>/dev/null; }
UI_URL="http://127.0.0.1:${UI_PORT}/healthz"
A2A_URL="http://127.0.0.1:${A2A_PORT}/.well-known/agent-card.json"

if up "$UI_URL"; then
  echo "chat UI:   already running on :${UI_PORT}"
else
  # 9>&- closes the lock descriptor for the child, or the long-running server would hold the lock forever
  nohup uvicorn app.server:app --host 0.0.0.0 --port "$UI_PORT" > /tmp/uvicorn.log 2>&1 9>&- &
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

[ -n "${STARTED_UI:-}" ]  && { wait_for "chat UI:   " "$UI_URL" /tmp/uvicorn.log;  }
[ -n "${STARTED_A2A:-}" ] && { wait_for "A2A agent: " "$A2A_URL" /tmp/a2a.log; }

# Never fail the Codespace lifecycle hook; the messages above say what went wrong.
exit 0
