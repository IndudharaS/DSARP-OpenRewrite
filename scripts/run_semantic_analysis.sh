#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPOSITORY=""
OUTPUT=""
JAVA_HOME_VALUE="${DSARP_JAVA_HOME_17:-}"
PYTHON="${DSARP_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
while (($#)); do
  case "$1" in
    --repository) REPOSITORY="${2:?missing path}"; shift 2 ;;
    --output) OUTPUT="${2:?missing path}"; shift 2 ;;
    --java-home) JAVA_HOME_VALUE="${2:?missing path}"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -d "$REPOSITORY" && -n "$OUTPUT" ]] || { echo "Repository and --output are required" >&2; exit 2; }
if [[ -x "$REPOSITORY/mvnw" ]]; then
  MAVEN=("$REPOSITORY/mvnw")
elif [[ -n "${DSARP_MAVEN:-}" && -x "$DSARP_MAVEN" ]]; then
  MAVEN=("$DSARP_MAVEN")
elif command -v mvn >/dev/null 2>&1; then
  MAVEN=(mvn)
else
  echo "No Maven executable is available for repository without mvnw: $REPOSITORY" >&2
  exit 1
fi
export JAVA_HOME="$JAVA_HOME_VALUE"
if [[ -n "${DSARP_SEMANTIC_MAVEN_OPTS:-}" ]]; then
  export MAVEN_OPTS="$DSARP_SEMANTIC_MAVEN_OPTS"
fi
echo "Semantic Maven JVM options: ${MAVEN_OPTS:-<Maven defaults>}"
"${MAVEN[@]}" -q -f "$PROJECT_ROOT/openrewrite-java/pom.xml" -DskipTests install

# OpenRewrite's aggregator resolves reactor SNAPSHOT dependencies before it
# visits all modules. Install the unchanged target reactor first so analysis
# does not fail merely because sibling artifacts are absent from this run's
# isolated Maven repository.
(
  cd "$REPOSITORY"
  "${MAVEN[@]}" -DskipTests install
)
analysis_marker="$(mktemp)"
trap 'rm -f "$analysis_marker"' EXIT
(
  cd "$REPOSITORY"
  "${MAVEN[@]}" -DskipTests \
    -Drewrite.recipeArtifactCoordinates=dsarp.rewrite:dsarp-openrewrite-recipes:1.0.0 \
    -Drewrite.activeRecipes=dsarp.rewrite.analysis.DependencyAnalysisRecipe \
    -Drewrite.exportDatatables=true \
    org.openrewrite.maven:rewrite-maven-plugin:6.12.0:dryRunNoFork
)
tables=()
while IFS= read -r table; do tables+=("$table"); done < <(
  find "$REPOSITORY" -type f -path '*/rewrite/datatables/*MethodDependencyTable*.csv' \
    -newer "$analysis_marker" -print | sort
)
((${#tables[@]})) || { echo "OpenRewrite did not export MethodDependencyTable CSV" >&2; exit 1; }
"$PYTHON" "$PROJECT_ROOT/openrewrite/convert_semantic_table.py" --input "${tables[@]}" --output "$OUTPUT"
