#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCAN_HOME="$PROJECT_ROOT/tools/arcan-1.2.1/distribution/arcan-1.2.1"
JAVA_HOME_VALUE="$PROJECT_ROOT/tools/java/jdk-17/Contents/Home"
REPOSITORY=""
OUTPUT_DIR=""

usage() {
  cat <<'EOF'
Usage: scripts/run_arcan_smells.sh --repository PATH --output-dir PATH [options]

Runs the pinned open-source Arcan 1.2.1 release on a repository's compiled
target/classes directories. The repository must be built before this command.

Options:
  --repository PATH   Built Java repository to analyze (required).
  --output-dir PATH   Directory for raw Arcan CSV files and logs (required).
  --java-home PATH    Java runtime used to launch Arcan.
  --arcan-home PATH   Extracted Arcan distribution directory.
  --help              Show this help.
EOF
}

while (($#)); do
  case "$1" in
    --repository) REPOSITORY="${2:?missing path}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:?missing path}"; shift 2 ;;
    --java-home) JAVA_HOME_VALUE="${2:?missing path}"; shift 2 ;;
    --arcan-home) ARCAN_HOME="${2:?missing path}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$REPOSITORY" ]] || { echo "--repository is required" >&2; exit 2; }
[[ -n "$OUTPUT_DIR" ]] || { echo "--output-dir is required" >&2; exit 2; }
REPOSITORY="$(cd "$REPOSITORY" && pwd)"
[[ -x "$JAVA_HOME_VALUE/bin/java" ]] || { echo "Java not found: $JAVA_HOME_VALUE/bin/java" >&2; exit 1; }
[[ -f "$ARCAN_HOME/arcan-1.2.1.jar" ]] || { echo "Arcan not found: $ARCAN_HOME" >&2; exit 1; }

if [[ -e "$OUTPUT_DIR" ]]; then
  backup="${OUTPUT_DIR}-backup-$(date +%Y%m%d-%H%M%S)"
  echo "Moving previous Arcan output to $backup"
  mv "$OUTPUT_DIR" "$backup"
fi
mkdir -p "$OUTPUT_DIR/input-classes" "$OUTPUT_DIR/raw"

class_count=0
while IFS= read -r -d '' classes_dir; do
  module="${classes_dir#"$REPOSITORY"/}"
  module="${module%/target/classes}"
  module="${module//\//__}"
  [[ -n "$module" ]] || module="root"
  while IFS= read -r -d '' class_file; do
    relative="${class_file#"$classes_dir"/}"
    destination="$OUTPUT_DIR/input-classes/$module/$relative"
    mkdir -p "$(dirname "$destination")"
    cp "$class_file" "$destination"
    class_count=$((class_count + 1))
  done < <(find "$classes_dir" -type f -name '*.class' ! -name 'module-info.class' -print0)
done < <(find "$REPOSITORY" -type d -path '*/target/classes' -print0)

((class_count > 0)) || {
  echo "No compiled classes found. Build the repository before running Arcan." >&2
  exit 1
}

printf 'Arcan input: %s compiled classes\n' "$class_count" | tee "$OUTPUT_DIR/input-summary.txt"
"$JAVA_HOME_VALUE/bin/java" \
  -cp "$ARCAN_HOME/arcan-1.2.1.jar:$ARCAN_HOME/lib/*" \
  it.unimib.disco.essere.main.TerminalExecutor \
  -p "$OUTPUT_DIR/input-classes" -class -all -out "$OUTPUT_DIR/raw" \
  >"$OUTPUT_DIR/arcan.log" 2>&1

required=(HL.csv UD.csv UD30.csv PM.csv CM.csv packageCyclicDependencyTable.csv classCyclicDependencyTable.csv)
for report in "${required[@]}"; do
  [[ -f "$OUTPUT_DIR/raw/$report" ]] || {
    echo "Arcan did not create $report; see $OUTPUT_DIR/arcan.log" >&2
    exit 1
  }
done

"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/evaluation/summarize_arcan.py" \
  summarize "$OUTPUT_DIR/raw" --output "$OUTPUT_DIR/summary.json" \
  --compiled-classes "$class_count" >/dev/null

echo "Arcan reports: $OUTPUT_DIR/raw"
echo "Arcan summary: $OUTPUT_DIR/summary.json"
