#!/usr/bin/env bash
# Run guitarz in the foreground (Ctrl-C to stop).
#
# Finds the venv from the script's own location, so it runs from any directory.
# For a background server use ./scripts/server.sh instead.
#
#   GUITARZ_ROOT  music folder to serve   (default: ~/gps, read by guitarz itself)
#   GUITARZ_PORT  TCP port                (default: 8008)
#   GUITARZ_HOST  bind address            (default: 127.0.0.1)
# Extra arguments are passed through to guitarz, e.g. ./start_guitarz.sh --port 9000
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUITARZ_BIN="$PROJECT_DIR/.venv/bin/guitarz"

PORT="${GUITARZ_PORT:-8008}"
HOST="${GUITARZ_HOST:-127.0.0.1}"

[ -x "$GUITARZ_BIN" ] || { echo "error: $GUITARZ_BIN not found — create the venv and 'pip install -e .' first" >&2; exit 1; }

exec "$GUITARZ_BIN" --host "$HOST" --port "$PORT" "$@"
