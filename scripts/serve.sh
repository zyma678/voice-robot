#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/runtime"
PID_FILE="$RUNTIME_DIR/server.pid"
LOG_FILE="$RUNTIME_DIR/server.log"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8090}"
APP_MODULE="${APP_MODULE:-voice_robot.server:app}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
UVICORN_BIN="${UVICORN_BIN:-$ROOT_DIR/.venv/bin/uvicorn}"
WS_PING_INTERVAL="${WS_PING_INTERVAL:-25}"
WS_PING_TIMEOUT="${WS_PING_TIMEOUT:-180}"

cmd="${1:-start}"

url() {
  printf "http://%s:%s" "$HOST" "$PORT"
}

is_pid_running() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

read_pid() {
  if [[ -f "$PID_FILE" ]]; then
    tr -d '[:space:]' < "$PID_FILE"
  fi
}

latest_log_pid() {
  if [[ -f "$LOG_FILE" ]]; then
    sed -n 's/.*Started server process \[\([0-9][0-9]*\)\].*/\1/p' "$LOG_FILE" | tail -1
  fi
}

recover_pid_file() {
  local pid
  pid="$(latest_log_pid || true)"
  if [[ -n "${pid:-}" ]]; then
    printf "%s\n" "$pid" > "$PID_FILE"
    echo "$pid"
  fi
}

is_http_ready() {
  curl -fsS --max-time 2 "$(url)/health" >/dev/null 2>&1
}

require_bins() {
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "ERROR: python not found: $PYTHON_BIN" >&2
    exit 1
  fi
  if [[ ! -x "$UVICORN_BIN" ]]; then
    echo "ERROR: uvicorn not found: $UVICORN_BIN" >&2
    exit 1
  fi
}

start_server() {
  require_bins
  mkdir -p "$RUNTIME_DIR"

  local pid
  pid="$(read_pid || true)"
  if [[ -n "${pid:-}" ]] && is_pid_running "$pid"; then
    echo "server already running: pid=$pid url=$(url)/console"
    return 0
  fi
  if [[ -n "${pid:-}" ]]; then
    rm -f "$PID_FILE"
  fi

  if is_http_ready; then
    pid="$(recover_pid_file || true)"
    if [[ -n "${pid:-}" ]]; then
      echo "server already running: pid=$pid url=$(url)/console"
    else
      echo "server is already reachable at $(url)/console"
      echo "WARN: $PID_FILE is missing or stale; stop may need the original pid." >&2
    fi
    return 0
  fi

  (
    cd "$ROOT_DIR"
    "$PYTHON_BIN" - "$UVICORN_BIN" "$APP_MODULE" "$HOST" "$PORT" "$WS_PING_INTERVAL" "$WS_PING_TIMEOUT" "$LOG_FILE" <<'PY'
import subprocess
import sys
from pathlib import Path

uvicorn_bin, app_module, host, port, ping_interval, ping_timeout, log_path = sys.argv[1:]
Path(log_path).parent.mkdir(parents=True, exist_ok=True)
log = open(log_path, "ab", buffering=0)
proc = subprocess.Popen(
    [
        uvicorn_bin,
        app_module,
        "--host",
        host,
        "--port",
        port,
        "--ws-ping-interval",
        ping_interval,
        "--ws-ping-timeout",
        ping_timeout,
    ],
    stdout=log,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
print(proc.pid)
PY
  ) > "$PID_FILE"

  pid="$(read_pid)"
  for _ in {1..75}; do
    if is_http_ready; then
      echo "server started: pid=$pid url=$(url)/console"
      echo "log: $LOG_FILE"
      return 0
    fi
    if ! is_pid_running "$pid"; then
      break
    fi
    sleep 0.2
  done

  echo "ERROR: server failed to start; see $LOG_FILE" >&2
  tail -40 "$LOG_FILE" >&2 || true
  exit 1
}

stop_server() {
  local pid
  pid="$(read_pid || true)"
  if [[ -z "${pid:-}" ]]; then
    pid="$(recover_pid_file || true)"
  fi
  if [[ -z "${pid:-}" ]]; then
    if is_http_ready; then
      echo "server is reachable at $(url), but $PID_FILE is missing." >&2
      echo "Cannot stop safely without a pid file." >&2
      exit 1
    fi
    echo "server not running"
    return 0
  fi

  if ! kill "$pid" 2>/dev/null; then
    if is_http_ready; then
      echo "server is reachable at $(url), but pid=$pid could not be stopped." >&2
      exit 1
    fi
    rm -f "$PID_FILE"
    echo "server not running; removed stale pid file"
    return 0
  fi

  for _ in {1..30}; do
    if ! is_http_ready && ! is_pid_running "$pid"; then
      rm -f "$PID_FILE"
      echo "server stopped: pid=$pid"
      return 0
    fi
    sleep 0.2
  done

  echo "server did not stop after SIGTERM; sending SIGKILL" >&2
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "server stopped: pid=$pid"
}

status_server() {
  local pid
  pid="$(read_pid || true)"
  if is_http_ready; then
    if [[ -z "${pid:-}" ]]; then
      pid="$(recover_pid_file || true)"
    fi
    if [[ -n "${pid:-}" ]] && is_pid_running "$pid"; then
      echo "running: pid=$pid url=$(url)/console"
    elif [[ -n "${pid:-}" ]]; then
      echo "running: url=$(url)/console pid_file=$pid"
    else
      echo "running: url=$(url)/console pid_file=missing"
    fi
    return 0
  fi

  if [[ -n "${pid:-}" ]] && is_pid_running "$pid"; then
    if is_http_ready; then
      echo "running: pid=$pid url=$(url)/console"
    else
      echo "starting or unhealthy: pid=$pid url=$(url)/console"
      exit 1
    fi
    return 0
  fi

  echo "stopped"
}

case "$cmd" in
  start)
    start_server
    ;;
  stop)
    stop_server
    ;;
  restart)
    stop_server || true
    start_server
    ;;
  status)
    status_server
    ;;
  logs)
    tail -f "$LOG_FILE"
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status|logs]" >&2
    echo "Environment: HOST=$HOST PORT=$PORT" >&2
    exit 2
    ;;
esac
