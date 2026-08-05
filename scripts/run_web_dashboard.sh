#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${DSARP_PYTHON:-$ROOT/.venv/bin/python}"
if [[ "${1:-}" == "--hpc" ]]; then
  shift
  PROJECT_SPACE="${DSARP_HPC_PROJECT_SPACE:-/scratch/hpc-prf-dssecs/$USER}"
  exec "$PYTHON" "$ROOT/webui/server.py" \
    --execution-mode hpc \
    --hpc-project-space "$PROJECT_SPACE" \
    "$@"
fi
exec "$PYTHON" "$ROOT/webui/server.py" "$@"
