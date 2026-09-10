#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${DSARP_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" \
  "$PROJECT_ROOT/openrewrite/generate_recipes.py" "$@"
