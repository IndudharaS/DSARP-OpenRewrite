#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${DSARP_PYTHON:-$ROOT/.venv/bin/python}"
exec "$PYTHON" "$ROOT/webui/server.py" "$@"
