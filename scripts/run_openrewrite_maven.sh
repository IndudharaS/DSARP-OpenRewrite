#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY=""
JAVA_HOME_VALUE=""
RECIPE=""
ACTIVE_RECIPE=""
RESULTS_DIR=""
LOG_DIR=""
MODE="all"
REWRITE_PLUGIN_VERSION="6.12.0"

usage() {
  cat <<'EOF'
Usage: scripts/run_openrewrite_maven.sh --repository PATH --java-home PATH \
  --recipe FILE --active-recipe NAME [options]

Generic OpenRewrite executor for a Maven project that has an executable mvnw.

Options:
  --results-dir PATH          Output directory for the dry-run patch.
  --log-dir PATH              Output directory for Maven logs.
  --mode dry-run|apply|all    Operation to perform (default: all).
  --plugin-version VERSION    rewrite-maven-plugin version (default: 6.12.0).
EOF
}

while (($#)); do
  case "$1" in
    --repository) REPOSITORY="${2:?missing path}"; shift 2 ;;
    --java-home) JAVA_HOME_VALUE="${2:?missing path}"; shift 2 ;;
    --recipe) RECIPE="${2:?missing path}"; shift 2 ;;
    --active-recipe) ACTIVE_RECIPE="${2:?missing name}"; shift 2 ;;
    --results-dir) RESULTS_DIR="${2:?missing path}"; shift 2 ;;
    --log-dir) LOG_DIR="${2:?missing path}"; shift 2 ;;
    --mode) MODE="${2:?missing mode}"; shift 2 ;;
    --plugin-version) REWRITE_PLUGIN_VERSION="${2:?missing version}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$REPOSITORY" && -n "$JAVA_HOME_VALUE" && -n "$RECIPE" && -n "$ACTIVE_RECIPE" ]] || {
  usage >&2; exit 2;
}
[[ "$MODE" =~ ^(dry-run|apply|all)$ ]] || { echo "Invalid --mode: $MODE" >&2; exit 2; }
[[ -x "$REPOSITORY/mvnw" ]] || { echo "Maven wrapper missing: $REPOSITORY/mvnw" >&2; exit 1; }
[[ -x "$JAVA_HOME_VALUE/bin/java" ]] || { echo "Java missing: $JAVA_HOME_VALUE/bin/java" >&2; exit 1; }
[[ -f "$RECIPE" ]] || { echo "Recipe missing: $RECIPE" >&2; exit 1; }

REPOSITORY="$(cd "$REPOSITORY" && pwd)"
JAVA_HOME_VALUE="$(cd "$JAVA_HOME_VALUE" && pwd)"
RECIPE="$(cd "$(dirname "$RECIPE")" && pwd)/$(basename "$RECIPE")"
RESULTS_DIR="${RESULTS_DIR:-$REPOSITORY/target/rewrite-results}"
LOG_DIR="${LOG_DIR:-$REPOSITORY/target/rewrite-logs}"
mkdir -p "$RESULTS_DIR" "$LOG_DIR"

run_rewrite() {
  local goal="$1" log_file="$2"
  (
    cd "$REPOSITORY"
    JAVA_HOME="$JAVA_HOME_VALUE" ./mvnw -DskipTests \
      -Drewrite.configLocation="$RECIPE" \
      -Drewrite.activeRecipes="$ACTIVE_RECIPE" \
      "org.openrewrite.maven:rewrite-maven-plugin:$REWRITE_PLUGIN_VERSION:$goal"
  ) 2>&1 | tee "$log_file"
}

if [[ "$MODE" == "dry-run" || "$MODE" == "all" ]]; then
  run_rewrite dryRunNoFork "$LOG_DIR/rewrite-dry-run.log"
  patch="$REPOSITORY/target/rewrite/rewrite.patch"
  [[ -f "$patch" ]] && cp "$patch" "$RESULTS_DIR/openrewrite-dry-run.patch"
fi

if [[ "$MODE" == "apply" || "$MODE" == "all" ]]; then
  run_rewrite runNoFork "$LOG_DIR/rewrite-run.log"
fi

git --no-pager -C "$REPOSITORY" status --short
git --no-pager -C "$REPOSITORY" diff --stat
