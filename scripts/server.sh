#!/usr/bin/env bash
# Start/stop/restart/status for the guitarz dev server.
#
# The server is a foreground uvicorn process; this wrapper daemonizes it with
# nohup, tracks it via a pidfile, and logs to guitarz.log so "how do I get the
# app back up" is always: ./scripts/server.sh start
#
# Configuration via environment (all optional):
#   GUITARZ_ROOT  music folder to serve   (default: <project>/gps)
#   GUITARZ_PORT  TCP port                (default: 8008)
#   GUITARZ_HOST  bind address            (default: 127.0.0.1)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GUITARZ_BIN="$PROJECT_DIR/.venv/bin/guitarz"
PID_FILE="$PROJECT_DIR/.guitarz.pid"
LOG_FILE="$PROJECT_DIR/guitarz.log"

ROOT="${GUITARZ_ROOT:-$PROJECT_DIR/gps}"
PORT="${GUITARZ_PORT:-8008}"
HOST="${GUITARZ_HOST:-127.0.0.1}"

# Print the PID from the pidfile if that process is still alive, else nothing.
alive_pid() {
    [ -f "$PID_FILE" ] || return 0
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
        echo "$pid"
    else
        rm -f "$PID_FILE"   # stale pidfile from a crash/reboot
    fi
}

start() {
    local pid
    pid="$(alive_pid)"
    if [ -n "$pid" ]; then
        echo "guitarz already running (pid $pid) at http://$HOST:$PORT"
        return 0
    fi
    [ -x "$GUITARZ_BIN" ] || { echo "error: $GUITARZ_BIN not found — create the venv and 'pip install -e .' first"; exit 1; }
    nohup "$GUITARZ_BIN" --root "$ROOT" --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    # Wait until the API answers so "start" failing is loud, not silent.
    for _ in $(seq 1 40); do
        if curl -fs -o /dev/null "http://$HOST:$PORT/api/browse?path="; then
            echo "guitarz started (pid $(cat "$PID_FILE")) — open http://$HOST:$PORT"
            return 0
        fi
        sleep 0.25
    done
    echo "error: server did not come up; last log lines:"
    tail -5 "$LOG_FILE"
    exit 1
}

stop() {
    local pid
    pid="$(alive_pid)"
    if [ -z "$pid" ]; then
        echo "guitarz is not running"
        return 0
    fi
    kill "$pid"
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
    rm -f "$PID_FILE"
    echo "guitarz stopped (pid $pid)"
}

status() {
    local pid
    pid="$(alive_pid)"
    if [ -n "$pid" ]; then
        echo "guitarz running (pid $pid) at http://$HOST:$PORT — serving $ROOT"
    else
        echo "guitarz is not running"
        return 1
    fi
}

case "${1:-}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; start ;;
    status)  status ;;
    *) echo "usage: $0 {start|stop|restart|status}"; exit 2 ;;
esac
