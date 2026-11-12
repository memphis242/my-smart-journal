#!/usr/bin/env bash
# Usage: backend.sh setup [--new-token] | start | stop | status | token-file
# Runs the real backend (msj serve + msj worker) on fake model routes for the Android e2e tests.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

MSJ="$MSJ_VENV/bin/msj"
SERVE_PID="$MSJ_PID_DIR/serve.pid"
WORKER_PID="$MSJ_PID_DIR/worker.pid"
BASE_URL="http://127.0.0.1:$MSJ_E2E_PORT"

usage() { sed -n '2p' "${BASH_SOURCE[0]}" | sed 's/^# //' >&2; exit 2; }

backend_env() {
  export MSJ_CONFIG_FILE MSJ_DATA_DIR PYTHONDONTWRITEBYTECODE=1
}

pid_alive() { # pid file -> 0 when the recorded process is alive
  local f="$1" pid
  [ -f "$f" ] || return 1
  pid="$(cat "$f")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

cmd_setup() {
  local new_token=0
  case "${1:-}" in
    "") ;;
    --new-token) new_token=1 ;;
    *) usage ;;
  esac
  [ -x /usr/bin/python3.14 ] || msj_die "/usr/bin/python3.14 not found; install Python 3.14 to build the backend venv."
  [ -f "$MSJ_CONFIG_FILE" ] || msj_die "backend config $MSJ_CONFIG_FILE is missing; is the repo checkout complete?"
  mkdir -p "$MSJ_LOG_DIR" "$MSJ_PID_DIR" "$MSJ_BACKEND_DIR"
  if [ ! -x "$MSJ_VENV/bin/pip" ]; then
    /usr/bin/python3.14 -m venv "$MSJ_VENV" >"$MSJ_LOG_DIR/setup.log" 2>&1 \
      || msj_die "creating venv at $MSJ_VENV failed; see $MSJ_LOG_DIR/setup.log"
  fi
  if [ ! -x "$MSJ" ]; then
    "$MSJ_VENV/bin/pip" install -e "$MSJ_REPO_ROOT/backend" >>"$MSJ_LOG_DIR/setup.log" 2>&1 \
      || msj_die "pip install of $MSJ_REPO_ROOT/backend failed; see $MSJ_LOG_DIR/setup.log"
  fi
  mkdir -p "$MSJ_DATA_DIR"; chmod 700 "$MSJ_DATA_DIR"
  backend_env
  "$MSJ" migrate >>"$MSJ_LOG_DIR/setup.log" 2>&1 \
    || msj_die "msj migrate failed against $MSJ_DATA_DIR; see $MSJ_LOG_DIR/setup.log"
  if [ ! -s "$MSJ_TOKEN_FILE" ] || [ "$new_token" = 1 ]; then
    if pid_alive "$SERVE_PID"; then
      msj_die "a new token ends every session; stop the backend first (backend.sh stop), then rerun setup --new-token."
    fi
    local tmp="$MSJ_TOKEN_FILE.tmp"
    ( umask 077; "$MSJ" create-token >"$tmp" 2>>"$MSJ_LOG_DIR/setup.log" ) \
      || { rm -f "$tmp"; msj_die "msj create-token failed; see $MSJ_LOG_DIR/setup.log"; }
    [ -s "$tmp" ] || { rm -f "$tmp"; msj_die "msj create-token produced no output; see $MSJ_LOG_DIR/setup.log"; }
    mv "$tmp" "$MSJ_TOKEN_FILE"
    chmod 600 "$MSJ_TOKEN_FILE"
  fi
  echo "backend setup done: venv=$MSJ_VENV data=$MSJ_DATA_DIR token file=$MSJ_TOKEN_FILE"
}

wait_health() {
  local i
  for i in $(seq 1 60); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$BASE_URL/api/v1/health" || true)" = 200 ]; then
      return 0
    fi
    if ! pid_alive "$SERVE_PID"; then
      msj_die "msj serve exited during startup; see $MSJ_LOG_DIR/serve.log"
    fi
    sleep 0.5
  done
  msj_die "backend did not answer $BASE_URL/api/v1/health within 30 s; see $MSJ_LOG_DIR/serve.log (is port $MSJ_E2E_PORT taken? check: ss -ltn 'sport = :$MSJ_E2E_PORT')"
}

cmd_start() {
  [ -x "$MSJ" ] && [ -s "$MSJ_TOKEN_FILE" ] || msj_die "backend not set up; run: $0 setup"
  if pid_alive "$SERVE_PID" || pid_alive "$WORKER_PID"; then
    msj_die "backend already running (see '$0 status'); stop it first with '$0 stop'."
  fi
  if ss -ltn "sport = :$MSJ_E2E_PORT" | grep -q LISTEN; then
    msj_die "port $MSJ_E2E_PORT is already in use by a process this harness did not start; set MSJ_E2E_PORT to a free port."
  fi
  mkdir -p "$MSJ_LOG_DIR" "$MSJ_PID_DIR"
  backend_env
  nohup "$MSJ" serve --host 127.0.0.1 --port "$MSJ_E2E_PORT" >"$MSJ_LOG_DIR/serve.log" 2>&1 &
  echo $! >"$SERVE_PID"
  nohup "$MSJ" worker >"$MSJ_LOG_DIR/worker.log" 2>&1 &
  echo $! >"$WORKER_PID"
  wait_health
  sleep 1
  pid_alive "$WORKER_PID" || msj_die "msj worker exited during startup; see $MSJ_LOG_DIR/worker.log"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "@-" "$BASE_URL/api/v1/session" <<<"Authorization: Bearer $(cat "$MSJ_TOKEN_FILE")" || true)"
  [ "$code" = 204 ] || msj_die "bearer check GET /api/v1/session returned $code (expected 204); the token file may be stale. Try: $0 stop; $0 setup --new-token; $0 start. Logs: $MSJ_LOG_DIR/serve.log"
  echo "backend up on $BASE_URL (serve pid $(cat "$SERVE_PID"), worker pid $(cat "$WORKER_PID")); logs in $MSJ_LOG_DIR"
}

stop_one() { # name pidfile
  local name="$1" f="$2" pid i
  if [ ! -f "$f" ]; then echo "$name: no pid file"; return 0; fi
  pid="$(cat "$f")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for i in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    if kill -0 "$pid" 2>/dev/null; then
      echo "$name (pid $pid) ignored SIGTERM for 10 s; sending SIGKILL" >&2
      kill -9 "$pid"
    fi
    echo "$name: stopped pid $pid"
  else
    echo "$name: pid $pid not running"
  fi
  rm -f "$f"
}

cmd_stop() {
  stop_one serve "$SERVE_PID"
  stop_one worker "$WORKER_PID"
}

cmd_status() {
  local rc=0 n f
  for n in serve worker; do
    f="$MSJ_PID_DIR/$n.pid"
    if pid_alive "$f"; then echo "$n: running (pid $(cat "$f"))"; else echo "$n: not running"; rc=1; fi
  done
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$BASE_URL/api/v1/health" || true)" = 200 ]; then
    echo "health: ok at $BASE_URL"
  else
    echo "health: no answer at $BASE_URL"; rc=1
  fi
  return $rc
}

case "${1:-}" in
  setup) shift; cmd_setup "$@" ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  token-file) echo "$MSJ_TOKEN_FILE" ;;
  *) usage ;;
esac
