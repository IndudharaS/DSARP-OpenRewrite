#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REPOSITORY=""
JAVA_HOME_17="$PROJECT_ROOT/tools/java/jdk-17/Contents/Home"
RECIPE="$PROJECT_ROOT/openrewrite/recipes/log4j2-move-file-size.yml"
FACADE_TEMPLATE="$PROJECT_ROOT/openrewrite/templates/FileSizeCompatibilityFacade.java"
RESULTS_DIR=""
LOG_DIR=""
MODE="all"
ACTIVE_RECIPE="masters.project.log4j2.MoveFileSizeToRollingAction"

usage() {
  cat <<'EOF'
Usage: scripts/run_openrewrite_log4j2.sh --repository PATH [options]

Required:
  --repository PATH      Clean Log4j2 experiment worktree to modify.

Options:
  --java-home PATH       JDK 17 home.
  --recipe PATH          OpenRewrite YAML recipe.
  --active-recipe NAME   Fully qualified recipe name inside the YAML file.
  --results-dir PATH     Directory receiving openrewrite-dry-run.patch.
  --log-dir PATH         Directory receiving OpenRewrite logs.
  --mode MODE            dry-run, apply, compatibility, or all (default).
  --help                 Show this help.

The 'all' mode performs a dry run, applies the recipe, installs the public-API
compatibility facade, restores the old-package facade test, and checks the diff.
EOF
}

while (($#)); do
  case "$1" in
    --repository) REPOSITORY="${2:?missing path after --repository}"; shift 2 ;;
    --java-home) JAVA_HOME_17="${2:?missing path after --java-home}"; shift 2 ;;
    --recipe) RECIPE="${2:?missing path after --recipe}"; shift 2 ;;
    --active-recipe) ACTIVE_RECIPE="${2:?missing name after --active-recipe}"; shift 2 ;;
    --results-dir) RESULTS_DIR="${2:?missing path after --results-dir}"; shift 2 ;;
    --log-dir) LOG_DIR="${2:?missing path after --log-dir}"; shift 2 ;;
    --mode) MODE="${2:?missing mode after --mode}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$REPOSITORY" ]] || { echo "--repository is required" >&2; usage >&2; exit 2; }
[[ "$MODE" =~ ^(dry-run|apply|compatibility|all)$ ]] || { echo "Invalid --mode: $MODE" >&2; exit 2; }
[[ -d "$REPOSITORY/.git" || -f "$REPOSITORY/.git" ]] || { echo "Not a Git worktree: $REPOSITORY" >&2; exit 1; }
[[ -x "$REPOSITORY/mvnw" ]] || { echo "Maven wrapper missing: $REPOSITORY/mvnw" >&2; exit 1; }
[[ -x "$JAVA_HOME_17/bin/java" ]] || { echo "JDK missing: $JAVA_HOME_17" >&2; exit 1; }
[[ -f "$RECIPE" ]] || { echo "Recipe missing: $RECIPE" >&2; exit 1; }
if [[ "$MODE" == "compatibility" || "$MODE" == "all" ]]; then
  [[ -f "$FACADE_TEMPLATE" ]] || { echo "Facade template missing: $FACADE_TEMPLATE" >&2; exit 1; }
fi

REPOSITORY="$(cd "$REPOSITORY" && pwd)"
JAVA_HOME_17="$(cd "$JAVA_HOME_17" && pwd)"
RECIPE="$(cd "$(dirname "$RECIPE")" && pwd)/$(basename "$RECIPE")"

RESULTS_DIR="${RESULTS_DIR:-$REPOSITORY/target/rewrite-results}"
LOG_DIR="${LOG_DIR:-$REPOSITORY/target/rewrite-logs}"
mkdir -p "$RESULTS_DIR" "$LOG_DIR"

run_rewrite() {
  local goal="$1" log_file="$2"
  (
    cd "$REPOSITORY"
    JAVA_HOME="$JAVA_HOME_17" ./mvnw \
      -Prewrite \
      -pl log4j-core,log4j-core-test \
      -am \
      -DskipTests \
      -Drewrite.configLocation="$RECIPE" \
      -Drewrite.activeRecipes="$ACTIVE_RECIPE" \
      compile "$goal"
  ) 2>&1 | tee "$log_file"
}

install_compatibility_facade() {
  local facade_target test_file
  facade_target="$REPOSITORY/log4j-core/src/main/java/org/apache/logging/log4j/core/appender/rolling/FileSize.java"
  test_file="$REPOSITORY/log4j-core-test/src/test/java/org/apache/logging/log4j/core/appender/rolling/FileSizeTest.java"

  cp "$FACADE_TEMPLATE" "$facade_target"

  "$PROJECT_ROOT/.venv/bin/python" - "$test_file" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace(
    "import org.apache.logging.log4j.core.appender.rolling.action.FileSize;\n",
    "",
)
path.write_text(text, encoding="utf-8")
PY

  git -C "$REPOSITORY" diff --check
}

if [[ "$MODE" == "dry-run" || "$MODE" == "all" ]]; then
  echo "Running OpenRewrite dry run with $RECIPE"
  run_rewrite rewrite:dryRunNoFork "$LOG_DIR/rewrite-dry-run.log"
  [[ -f "$REPOSITORY/target/rewrite/rewrite.patch" ]] || {
    echo "OpenRewrite did not generate target/rewrite/rewrite.patch" >&2
    exit 1
  }
  cp "$REPOSITORY/target/rewrite/rewrite.patch" "$RESULTS_DIR/openrewrite-dry-run.patch"
fi

if [[ "$MODE" == "apply" || "$MODE" == "all" ]]; then
  echo "Applying OpenRewrite recipe $ACTIVE_RECIPE"
  run_rewrite rewrite:runNoFork "$LOG_DIR/rewrite-run.log"
fi

if [[ "$MODE" == "compatibility" || "$MODE" == "all" ]]; then
  if grep -q 'org.apache.logging.log4j.core.appender.rolling.FileSize' "$RECIPE"; then
    echo "Installing Log4j2 FileSize public-API compatibility facade"
    install_compatibility_facade
  elif [[ "$MODE" == "compatibility" ]]; then
    echo "No registered compatibility strategy matches this recipe" >&2
    exit 1
  else
    echo "Validated aggregate does not require a registered compatibility facade."
  fi
fi

echo
echo "OpenRewrite operation complete."
echo "Repository: $REPOSITORY"
echo "Recipe:     $RECIPE"
echo "Results:    $RESULTS_DIR"
echo "Logs:       $LOG_DIR"
git --no-pager -C "$REPOSITORY" status --short
git --no-pager -C "$REPOSITORY" diff --stat
