#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_SPACE="${DSARP_HPC_PROJECT_SPACE:-/scratch/hpc-prf-dssecs/$USER}"
PROJECT_ROOT="${DSARP_PROJECT_ROOT:-$PROJECT_SPACE/dsarp-openrewrite}"
STATE_DIR="$PROJECT_ROOT/webui/state"
PID_FILE="$STATE_DIR/hpc-dashboard.pid"
LOG_FILE="$STATE_DIR/hpc-dashboard.log"

running_pid() {
  [[ -s "$PID_FILE" ]] || return 1
  local pid
  pid="$(<"$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s\n' "$pid"
}

case "${1:-}" in
  start)
    if pid="$(running_pid)"; then
      echo "HPC dashboard is already running (PID $pid)."
      echo "Log: $LOG_FILE"
      exit 0
    fi
    mkdir -p "$STATE_DIR"
    module reset
    module load lang
    module load Python/3.12.3-GCCcore-13.3.0
    export DSARP_PYTHON="${DSARP_PYTHON:-$PROJECT_SPACE/environments/dsarp-python-3.12/bin/python}"
    cd "$PROJECT_ROOT"
    nohup scripts/run_web_dashboard.sh --hpc >"$LOG_FILE" 2>&1 </dev/null &
    pid=$!
    printf '%s\n' "$pid" >"$PID_FILE"
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Dashboard failed to start. Last log lines:" >&2
      tail -n 30 "$LOG_FILE" >&2 || true
      exit 1
    fi
    echo "HPC dashboard started (PID $pid)."
    echo "URL through SSH tunnel: http://127.0.0.1:8765"
    echo "Log: $LOG_FILE"
    ;;
  status)
    if pid="$(running_pid)"; then
      echo "HPC dashboard is running (PID $pid)."
      if command -v curl >/dev/null 2>&1; then
        curl --max-time 3 --silent --show-error http://127.0.0.1:8765/api/health || true
        echo
      fi
    else
      echo "HPC dashboard is not running."
      exit 1
    fi
    ;;
  logs)
    touch "$LOG_FILE"
    tail -F "$LOG_FILE"
    ;;
  stop)
    if pid="$(running_pid)"; then
      kill "$pid"
      rm -f "$PID_FILE"
      echo "Stopped HPC dashboard PID $pid. Slurm pipeline jobs were not cancelled."
    else
      rm -f "$PID_FILE"
      echo "HPC dashboard was not running."
    fi
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  *)
    echo "Usage: hpc/manage_dashboard.sh {start|status|logs|stop|restart}" >&2
    exit 2
    ;;
esac
