#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${DSARP_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"

[[ -x "$PYTHON" ]] || { echo "Python environment missing: $PYTHON" >&2; exit 1; }

cd "$PROJECT_ROOT"
"$PYTHON" -m unittest discover -s tests -v
"$PYTHON" -m py_compile evaluation/*.py ml/*.py openrewrite/*.py webui/server.py

for script in scripts/*.sh; do
  bash -n "$script"
done

if command -v node >/dev/null 2>&1; then
  node --check webui/static/app.js
else
  echo "Node.js not installed; JavaScript syntax check skipped."
fi

echo "Project checks passed."
