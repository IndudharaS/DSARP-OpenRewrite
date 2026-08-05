#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PROJECT_NAME="logging-log4j2"
REPOSITORY_URL="https://github.com/apache/logging-log4j2.git"
VERSION_ID="4f474b32751f4ccad67424ca585612584440cd63"
RUN_ROOT="$PROJECT_ROOT/runs/log4j2"
JAVA_HOME_17="${DSARP_JAVA_HOME_17:-$PROJECT_ROOT/tools/java/jdk-17/Contents/Home}"
JAVA_HOME_21="${DSARP_JAVA_HOME_21:-$PROJECT_ROOT/tools/java/jdk-21/Contents/Home}"
PYTHON="${DSARP_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
JUPYTER="${DSARP_JUPYTER:-$PROJECT_ROOT/.venv/bin/jupyter}"
REFMINER="${DSARP_REFACTORING_MINER:-$PROJECT_ROOT/tools/refactoring-miner/RefactoringMiner-3.1.4/bin/RefactoringMiner}"
ARCAN_HOME="${DSARP_ARCAN_HOME:-$PROJECT_ROOT/tools/arcan-1.2.1/distribution/arcan-1.2.1}"
OPENREWRITE_RUNNER="$PROJECT_ROOT/scripts/run_openrewrite_log4j2.sh"
OPENREWRITE_GENERATOR="$PROJECT_ROOT/scripts/generate_openrewrite_recipes.sh"
ARCAN_RUNNER="$PROJECT_ROOT/scripts/run_arcan_smells.sh"
CANDIDATE_VALIDATOR="$PROJECT_ROOT/evaluation/validate_openrewrite_candidates.py"
BASELINE_CSV_DIR="$PROJECT_ROOT/baseline_csv"
BASE_REPO="$RUN_ROOT/repositories/logging-log4j2"
REWRITE_REPO="$RUN_ROOT/repositories/logging-log4j2-openrewrite"
RESULTS_DIR="$RUN_ROOT/results"
LOG_DIR="$RUN_ROOT/logs"
MINING_DIR="$RUN_ROOT/mining"
MINING_OUTPUT="$RUN_ROOT/mining-output"
MINING_CACHE_DIR="$PROJECT_ROOT/shared/refactoring-miner/default"
MODEL_DIR="$RUN_ROOT/models/distilbert_improved_strict_ranked_top5"
MODEL_INPUTS="$PROJECT_ROOT/pipeline_results/logging_log4j2_model_inputs_from_csv.csv"
PREDICTIONS=""

FULL=0
CLEAN=0
PREDICTIONS_EXPLICIT=0
PROFILE="log4j2"
TRAINING_DATASET=""
REMINE=0
ALLOW_RISKY_CANDIDATES=0
SEVERITY_CATEGORIES="high,medium,low"
BATCH_SIZE=10
START_BATCH=1
MAX_BATCHES=0
START_STAGE="preflight"
STOP_STAGE="summary"

STAGES=(preflight inputs mining training prediction clone baseline rewrite focused_test format final_verify smells summary)

usage() {
  cat <<'EOF'
Usage: scripts/run_log4j2_pipeline.sh [options]

Options:
  --full                 Rerun mining, model training, and prediction generation.
                         Without this flag, the retained 716 Log4j2 predictions are used.
  --clean                Move an existing run directory to a timestamped backup first.
  --from STAGE           Resume at STAGE.
  --through STAGE        Stop after STAGE.
  --run-root PATH        Store repositories, logs, and results under PATH.
  --baseline-csv-dir PATH
                         Read the original system's three Arcan CSV files here.
  --repository-url URL   Target repository URL (defaults to Apache Log4j2).
  --version-id COMMIT    Exact target commit (defaults to the assigned commit).
  --project-name NAME    Name used for run repository directories.
  --model-inputs PATH    Model-input CSV used by the prediction notebook.
  --predictions-csv PATH Use an existing prediction CSV when not using --full.
  --training-dataset PATH
                         Reuse an existing mined JSONL dataset while retraining.
  --mining-cache-dir PATH
                         Shared RefactoringMiner cache used by CLI and web runs.
  --remine               Rebuild the shared mining cache instead of reusing it.
  --profile NAME         Execution profile: log4j2 or generic.
  --allow-risky-candidates
                         Execute high-risk public-API candidates in isolated
                         worktrees. Only Maven-validated changes are applied.
  --severity-categories LIST
                         Comma-separated high,medium,low categories (default: all).
  --batch-size NUMBER    Candidates validated per batch (default: 10).
  --start-batch NUMBER   One-based first batch to execute (default: 1).
  --max-batches NUMBER   Stop validation after this many batches; 0 means all.
  --help                 Show this help.

Stages:
  preflight inputs mining training prediction clone baseline rewrite focused_test
  format final_verify smells summary

Examples:
  scripts/run_log4j2_pipeline.sh
  scripts/run_log4j2_pipeline.sh --clean
  scripts/run_log4j2_pipeline.sh --baseline-csv-dir baseline_csv
  scripts/run_log4j2_pipeline.sh --full --clean
  scripts/run_log4j2_pipeline.sh --from focused_test
EOF
}

while (($#)); do
  case "$1" in
    --full) FULL=1; shift ;;
    --clean) CLEAN=1; shift ;;
    --from) START_STAGE="${2:?missing stage after --from}"; shift 2 ;;
    --through) STOP_STAGE="${2:?missing stage after --through}"; shift 2 ;;
    --run-root)
      RUN_ROOT="${2:?missing path after --run-root}"
      BASE_REPO="$RUN_ROOT/repositories/logging-log4j2"
      REWRITE_REPO="$RUN_ROOT/repositories/logging-log4j2-openrewrite"
      RESULTS_DIR="$RUN_ROOT/results"
      LOG_DIR="$RUN_ROOT/logs"
      MINING_DIR="$RUN_ROOT/mining"
      MINING_OUTPUT="$RUN_ROOT/mining-output"
      MODEL_DIR="$RUN_ROOT/models/distilbert_improved_strict_ranked_top5"
      shift 2
      ;;
    --baseline-csv-dir) BASELINE_CSV_DIR="${2:?missing path after --baseline-csv-dir}"; shift 2 ;;
    --repository-url) REPOSITORY_URL="${2:?missing URL}"; shift 2 ;;
    --version-id) VERSION_ID="${2:?missing commit}"; shift 2 ;;
    --project-name) PROJECT_NAME="${2:?missing name}"; shift 2 ;;
    --model-inputs) MODEL_INPUTS="${2:?missing path}"; shift 2 ;;
    --predictions-csv) PREDICTIONS="${2:?missing path}"; PREDICTIONS_EXPLICIT=1; shift 2 ;;
    --training-dataset) TRAINING_DATASET="${2:?missing path}"; shift 2 ;;
    --mining-cache-dir) MINING_CACHE_DIR="${2:?missing path}"; shift 2 ;;
    --remine) REMINE=1; shift ;;
    --profile) PROFILE="${2:?missing profile}"; shift 2 ;;
    --allow-risky-candidates) ALLOW_RISKY_CANDIDATES=1; shift ;;
    --severity-categories) SEVERITY_CATEGORIES="${2:?missing categories}"; shift 2 ;;
    --batch-size) BATCH_SIZE="${2:?missing batch size}"; shift 2 ;;
    --start-batch) START_BATCH="${2:?missing start batch}"; shift 2 ;;
    --max-batches) MAX_BATCHES="${2:?missing maximum batches}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

BASE_REPO="$RUN_ROOT/repositories/$PROJECT_NAME"
REWRITE_REPO="$RUN_ROOT/repositories/$PROJECT_NAME-openrewrite"
if ((!PREDICTIONS_EXPLICIT)); then
  PREDICTIONS="$PROJECT_ROOT/shared/pipeline-cache/$PROJECT_NAME/$VERSION_ID/predictions.csv"
fi
[[ "$PROFILE" =~ ^(log4j2|generic)$ ]] || { echo "Invalid --profile: $PROFILE" >&2; exit 2; }
[[ "$SEVERITY_CATEGORIES" =~ ^(high|medium|low)(,(high|medium|low))*$ ]] || { echo "Invalid --severity-categories: $SEVERITY_CATEGORIES" >&2; exit 2; }
[[ "$BATCH_SIZE" =~ ^[1-9][0-9]*$ ]] || { echo "--batch-size must be a positive integer" >&2; exit 2; }
[[ "$START_BATCH" =~ ^[1-9][0-9]*$ ]] || { echo "--start-batch must be a positive integer" >&2; exit 2; }
[[ "$MAX_BATCHES" =~ ^[0-9]+$ ]] || { echo "--max-batches must be zero or a positive integer" >&2; exit 2; }
if [[ "$PROFILE" == "generic" ]]; then
  OPENREWRITE_RUNNER="$PROJECT_ROOT/scripts/run_openrewrite_maven.sh"
  VALIDATOR_COMPATIBILITY_PROFILE="none"
else
  VALIDATOR_COMPATIBILITY_PROFILE="$PROFILE"
fi

# Notebooks execute with their own working directory, so every path embedded in
# a parameterized notebook must be absolute.
if [[ -n "$TRAINING_DATASET" && "$TRAINING_DATASET" != /* ]]; then
  TRAINING_DATASET="$(cd "$(dirname "$TRAINING_DATASET")" && pwd)/$(basename "$TRAINING_DATASET")"
fi
if [[ "$BASELINE_CSV_DIR" != /* ]]; then
  BASELINE_CSV_DIR="$(cd "$BASELINE_CSV_DIR" && pwd)"
fi
if [[ "$MODEL_INPUTS" != /* ]]; then
  MODEL_INPUTS="$(cd "$(dirname "$MODEL_INPUTS")" && pwd)/$(basename "$MODEL_INPUTS")"
fi
if [[ "$MINING_CACHE_DIR" != /* ]]; then
  MINING_CACHE_DIR="$PROJECT_ROOT/$MINING_CACHE_DIR"
fi
if ((PREDICTIONS_EXPLICIT)) && [[ "$PREDICTIONS" != /* ]]; then
  PREDICTIONS="$(cd "$(dirname "$PREDICTIONS")" && pwd)/$(basename "$PREDICTIONS")"
fi

stage_index() {
  local wanted="$1" index
  for index in "${!STAGES[@]}"; do
    [[ "${STAGES[$index]}" == "$wanted" ]] && { echo "$index"; return; }
  done
  echo "Unknown stage: $wanted" >&2
  exit 2
}

START_INDEX="$(stage_index "$START_STAGE")"
STOP_INDEX="$(stage_index "$STOP_STAGE")"
(( START_INDEX <= STOP_INDEX )) || { echo "--from must not follow --through" >&2; exit 2; }

should_run() {
  local index
  index="$(stage_index "$1")"
  (( index >= START_INDEX && index <= STOP_INDEX ))
}

heading() {
  mkdir -p "$RUN_ROOT"
  printf '%s\n' "$1" >"$RUN_ROOT/.pipeline-stage"
  printf '\n============================================================\n%s\n============================================================\n' "$1"
}

require_file() {
  [[ -f "$1" ]] || { echo "Required file missing: $1" >&2; exit 1; }
}

require_command() {
  command -v "$1" >/dev/null || { echo "Required command missing: $1" >&2; exit 1; }
}

run_expected_spring_failure() {
  local repository="$1" log_file="$2" status
  local verify_arguments=(verify)
  if [[ -n "${DSARP_MAVEN_TEST_EXCLUDES:-}" ]]; then
    verify_arguments+=(
      "-Dsurefire.excludes=$DSARP_MAVEN_TEST_EXCLUDES"
    )
    echo "Maven verification exclusions: $DSARP_MAVEN_TEST_EXCLUDES" | tee -a "$log_file"
  fi
  set +e
  (cd "$repository" && JAVA_HOME="$JAVA_HOME_17" ./mvnw "${verify_arguments[@]}") \
    2>&1 | tee -a "$log_file"
  status=${PIPESTATUS[0]}
  set -e

  if ((status == 0)); then
    echo "Full verification passed."
    return
  fi

  if grep -q "Log4j2SpringBootInitTest.testEnvironment" "$log_file" \
      && grep -Eq "expected: <1> but was: <5>|expected 1.*actual 5" "$log_file"; then
    echo "Accepted known baseline condition: Spring Boot test expected 1 message and observed 5."
    return
  fi

  echo "Unexpected Maven verification failure. See $log_file" >&2
  return "$status"
}

parameterize_notebook() {
  local source="$1" destination="$2" old_data="$3" new_data="$4" old_model="$5" new_model="$6"
  "$PYTHON" - "$source" "$destination" "$old_data" "$new_data" "$old_model" "$new_model" <<'PY'
import json
import sys
from pathlib import Path

source, destination, old_data, new_data, old_model, new_model = sys.argv[1:]
notebook = json.loads(Path(source).read_text(encoding="utf-8"))
notebook.setdefault("metadata", {})["kernelspec"] = {
    "display_name": "Python 3", "language": "python", "name": "python3"
}
found_split = False
found_trainer = False
for cell in notebook.get("cells", []):
    if cell.get("cell_type") != "code":
        continue
    text = "".join(cell.get("source", []))
    text = text.replace(old_data, new_data).replace(old_model, new_model)
    if "# Train / validation / test split" in text:
        found_split = True
        text = '''# Train / validation / test split grouped by commit
from sklearn.model_selection import GroupShuffleSplit

if "commit" in df.columns and df["commit"].nunique() >= 3:
    first_split = GroupShuffleSplit(n_splits=1, test_size=0.30, random_state=RANDOM_STATE)
    train_index, temp_index = next(first_split.split(df, groups=df["commit"]))
    train_df = df.iloc[train_index].copy()
    temp_df = df.iloc[temp_index].copy()
    second_split = GroupShuffleSplit(n_splits=1, test_size=0.50, random_state=RANDOM_STATE)
    val_index, test_index = next(second_split.split(temp_df, groups=temp_df["commit"]))
    val_df = temp_df.iloc[val_index].copy()
    test_df = temp_df.iloc[test_index].copy()
else:
    train_df, temp_df = train_test_split(df, test_size=0.30, random_state=RANDOM_STATE, shuffle=True)
    val_df, test_df = train_test_split(temp_df, test_size=0.50, random_state=RANDOM_STATE, shuffle=True)

print("Train:", len(train_df))
print("Validation:", len(val_df))
print("Test:", len(test_df))
'''
    if "# Train\n" in text and "trainer = Trainer(" in text:
        found_trainer = True
        text = '''# Train with bounded label-frequency weights
train_label_matrix = np.stack(train_df["labels"].to_numpy())
positive_counts = train_label_matrix.sum(axis=0)
negative_counts = len(train_label_matrix) - positive_counts
positive_weights = np.divide(
    negative_counts,
    np.maximum(positive_counts, 1),
).clip(1.0, 10.0)


class WeightedMultiLabelTrainer(Trainer):
    def __init__(self, *args, positive_weights, **kwargs):
        super().__init__(*args, **kwargs)
        self.positive_weights = torch.tensor(positive_weights, dtype=torch.float32)

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        loss_function = torch.nn.BCEWithLogitsLoss(
            pos_weight=self.positive_weights.to(outputs.logits.device)
        )
        loss = loss_function(outputs.logits, labels.float())
        return (loss, outputs) if return_outputs else loss


trainer = WeightedMultiLabelTrainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
    compute_metrics=compute_metrics,
    positive_weights=positive_weights,
)

trainer.train()
'''
    text = text.replace('metric_for_best_model="micro_f1"', 'metric_for_best_model="macro_f1"')
    cell["source"] = text.splitlines(keepends=True)
if not found_split or not found_trainer:
    raise SystemExit(
        f"Training notebook structure changed: split cell={found_split}, trainer cell={found_trainer}"
    )
rendered = json.dumps(notebook, indent=1)
if old_data in rendered or old_model in rendered:
    raise SystemExit("Training notebook parameterization left an original hard-coded path")
Path(destination).parent.mkdir(parents=True, exist_ok=True)
Path(destination).write_text(rendered, encoding="utf-8")
PY
}

if ((CLEAN)) && [[ -e "$RUN_ROOT" ]]; then
  backup="${RUN_ROOT}-backup-$(date +%Y%m%d-%H%M%S)"
  echo "Moving existing run to $backup"
  mv "$RUN_ROOT" "$backup"
fi

# A full run stores its generated predictions inside RUN_ROOT. When --clean
# moves that run to a backup, reuse the newest retained CSV for a non-full run
# unless the caller supplied --predictions-csv explicitly.
if ((!FULL && !PREDICTIONS_EXPLICIT)) && [[ ! -f "$PREDICTIONS" ]]; then
  prediction_name="${PROJECT_NAME}_refactoring_suggestions_from_trained_model.csv"
  retained_predictions=()
  while IFS= read -r candidate; do
    retained_predictions+=("$candidate")
  done < <(find "$(dirname "$RUN_ROOT")" -maxdepth 3 -type f \
    -path "$RUN_ROOT-backup-*/pipeline-results/$prediction_name" \
    -print 2>/dev/null | sort)
  if ((${#retained_predictions[@]})); then
    PREDICTIONS="${retained_predictions[${#retained_predictions[@]}-1]}"
    echo "Reusing retained predictions: $PREDICTIONS"
  fi
fi

mkdir -p "$RESULTS_DIR" "$LOG_DIR" "$RUN_ROOT/notebooks"

if should_run preflight; then
  heading "Stage: preflight"
  require_command git
  require_command jq
  require_file "$PYTHON"
  require_file "$JAVA_HOME_17/bin/java"
  require_file "$PROJECT_ROOT/arcan_matched_dataset_builder.py"
  require_file "$OPENREWRITE_RUNNER"
  require_file "$OPENREWRITE_GENERATOR"
  require_file "$ARCAN_RUNNER"
  require_file "$PROJECT_ROOT/evaluation/summarize_arcan.py"
  require_file "$PROJECT_ROOT/evaluation/build_experiment_report.py"
  require_file "$PROJECT_ROOT/evaluation/validate_baseline_inputs.py"
  require_file "$PROJECT_ROOT/evaluation/capture_provenance.py"
  require_file "$CANDIDATE_VALIDATOR"
  require_file "$PROJECT_ROOT/ml/predict_refactorings.py"
  require_file "$PROJECT_ROOT/ml/prepare_training_dataset.py"
  require_file "$PROJECT_ROOT/ml/evaluate_rankings.py"
  require_file "$BASELINE_CSV_DIR/component-metrics.csv"
  require_file "$BASELINE_CSV_DIR/smell-characteristics.csv"
  require_file "$BASELINE_CSV_DIR/smell-affects.csv"
  "$PYTHON" "$PROJECT_ROOT/evaluation/validate_baseline_inputs.py" \
    "$BASELINE_CSV_DIR" --project "$PROJECT_NAME" --version-id "$VERSION_ID" \
    --report "$RESULTS_DIR/baseline-input-validation.json" >/dev/null
  "$PYTHON" "$PROJECT_ROOT/evaluation/capture_provenance.py" \
    --output "$RESULTS_DIR/run-provenance.json" \
    --project "$PROJECT_NAME" --repository-url "$REPOSITORY_URL" \
    --version-id "$VERSION_ID" --profile "$PROFILE" \
    --java "$JAVA_HOME_17/bin/java" \
    --arcan-jar "$ARCAN_HOME/arcan-1.2.1.jar" \
    --refactoring-miner "$REFMINER"
  "$JAVA_HOME_17/bin/java" -version
  "$PYTHON" --version
  if ((FULL)); then
    require_file "$JUPYTER"
    require_file "$PROJECT_ROOT/csv_parser.ipynb"
    require_file "$PROJECT_ROOT/DISTILBERT_IMPROVED_DATA_TRAINED_MODEL.ipynb"
    if [[ -n "$TRAINING_DATASET" ]]; then
      require_file "$TRAINING_DATASET"
      [[ -s "$TRAINING_DATASET" ]] || { echo "Training dataset is empty: $TRAINING_DATASET" >&2; exit 1; }
    elif ((REMINE)) || [[ ! -s "$MINING_CACHE_DIR/output/arcan_style_training_dataset.jsonl" ]]; then
      require_file "$JAVA_HOME_21/bin/java"
      require_file "$REFMINER"
      JAVA_HOME="$JAVA_HOME_21" "$REFMINER" -h >/dev/null
    fi
  else
    require_file "$PREDICTIONS"
    retained_predictions="$RUN_ROOT/pipeline-results/${PROJECT_NAME}_refactoring_suggestions_from_trained_model.csv"
    if [[ "$PREDICTIONS" != "$retained_predictions" ]]; then
      mkdir -p "$(dirname "$retained_predictions")"
      cp "$PREDICTIONS" "$retained_predictions"
      printf '%s\n' "$PREDICTIONS" > "$RUN_ROOT/pipeline-results/predictions-source.txt"
      PREDICTIONS="$retained_predictions"
      echo "Archived reused predictions in this run: $PREDICTIONS"
    fi
  fi
fi

if should_run inputs; then
  heading "Stage: baseline CSV to model inputs"
  if ((FULL)); then
    INPUT_NOTEBOOK="$RUN_ROOT/notebooks/csv-parser.parameterized.ipynb"
    mkdir -p "$(dirname "$MODEL_INPUTS")"
    "$PYTHON" - \
      "$PROJECT_ROOT/csv_parser.ipynb" "$INPUT_NOTEBOOK" \
      "$BASELINE_CSV_DIR/component-metrics.csv" \
      "$BASELINE_CSV_DIR/smell-characteristics.csv" \
      "$BASELINE_CSV_DIR/smell-affects.csv" \
      "$MODEL_INPUTS" <<'PY'
import json
import re
import sys
from pathlib import Path

source, destination, component, characteristics, affects, output = sys.argv[1:]
notebook = json.loads(Path(source).read_text(encoding="utf-8"))
notebook.setdefault("metadata", {})["kernelspec"] = {
    "display_name": "Python 3", "language": "python", "name": "python3"
}
values = {
    "COMPONENT_METRICS_PATH": str(Path(component).resolve()),
    "SMELL_CHARACTERISTICS_PATH": str(Path(characteristics).resolve()),
    "SMELL_AFFECTS_PATH": str(Path(affects).resolve()),
    "OUTPUT_INPUTS_PATH": str(Path(output).resolve()),
}
replacements = {key: 0 for key in values}
for cell in notebook.get("cells", []):
    if cell.get("cell_type") != "code":
        continue
    text = "".join(cell.get("source", []))
    for variable, value in values.items():
        replacement = f"{variable} = Path({value!r})"
        text, count = re.subn(rf"^{variable}\s*=\s*Path\([^\n]+\)$", replacement, text, flags=re.MULTILINE)
        replacements[variable] += count
    cell["source"] = text.splitlines(keepends=True)
missing = [name for name, count in replacements.items() if count != 1]
if missing:
    raise SystemExit(f"CSV notebook parameterization expected one assignment for: {missing}; got {replacements}")
Path(destination).parent.mkdir(parents=True, exist_ok=True)
Path(destination).write_text(json.dumps(notebook, indent=1), encoding="utf-8")
PY
    "$JUPYTER" nbconvert --execute --to notebook \
      --ExecutePreprocessor.timeout=-1 \
      --output "$RUN_ROOT/notebooks/csv-parser.executed.ipynb" \
      "$INPUT_NOTEBOOK" | tee "$LOG_DIR/csv-parser.log"
    require_file "$MODEL_INPUTS"
    "$PYTHON" - "$MODEL_INPUTS" <<'PY'
import csv, sys
with open(sys.argv[1], newline="", encoding="utf-8-sig") as handle:
    rows = list(csv.DictReader(handle))
if not rows:
    raise SystemExit("CSV parser generated no model-input records")
print(f"Model-input records: {len(rows):,}")
PY
  else
    echo "Skipped. Existing predictions are being used; use --full to regenerate model inputs."
  fi
fi

if should_run mining; then
  heading "Stage: mining"
  if ((FULL)); then
    if [[ -n "$TRAINING_DATASET" ]]; then
      echo "Skipped RefactoringMiner; reusing training dataset: $TRAINING_DATASET"
    else
      cache_parent="$(dirname "$MINING_CACHE_DIR")"
      cache_name="$(basename "$MINING_CACHE_DIR")"
      cache_lock="$cache_parent/.${cache_name}.lock"
      mkdir -p "$cache_parent"
      while ! mkdir "$cache_lock" 2>/dev/null; do
        owner="$(sed -n '1p' "$cache_lock/pid" 2>/dev/null || true)"
        if [[ -n "$owner" ]] && ! kill -0 "$owner" 2>/dev/null; then
          echo "Removing stale mining-cache lock owned by PID $owner"
          rm -rf "$cache_lock"
          continue
        fi
        echo "Another experiment is updating the shared mining cache; waiting..."
        sleep 5
      done
      echo "$$" >"$cache_lock/pid"
      trap '[[ -n "${cache_lock:-}" && -d "$cache_lock" ]] && rm -rf "$cache_lock"' EXIT INT TERM

      cache_dataset="$MINING_CACHE_DIR/output/arcan_style_training_dataset.jsonl"
      if ((!REMINE)) && [[ -s "$cache_dataset" ]]; then
        echo "Reusing shared RefactoringMiner output: $MINING_CACHE_DIR/output"
      else
        if ((REMINE)) && [[ -d "$MINING_CACHE_DIR" ]]; then
          cache_backup="$cache_parent/${cache_name}-backup-$(date +%Y%m%d-%H%M%S)"
          echo "Moving previous shared mining cache to $cache_backup"
          mv "$MINING_CACHE_DIR" "$cache_backup"
        fi
        mkdir -p "$MINING_CACHE_DIR"
        JAVA_HOME="$JAVA_HOME_21" "$PYTHON" "$PROJECT_ROOT/arcan_matched_dataset_builder.py" \
          --work-dir "$MINING_CACHE_DIR/work" \
          --output-dir "$MINING_CACHE_DIR/output" \
          --refactoring-miner "$REFMINER" \
          --max-commits-per-repo 500 \
          --top-k-labels 5 | tee "$LOG_DIR/mining.log"
        cache_dataset="$MINING_CACHE_DIR/output/arcan_style_training_dataset.jsonl"
        "$PYTHON" - "$MINING_CACHE_DIR/cache-manifest.json" "$cache_dataset" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path

manifest, dataset = map(Path, sys.argv[1:])
records = sum(1 for line in dataset.open(encoding="utf-8") if line.strip())
manifest.write_text(json.dumps({
    "createdAt": datetime.now(timezone.utc).isoformat(),
    "repositories": ["tika", "maven", "camel", "ant", "lucene"],
    "maxCommitsPerRepository": 500,
    "maximumCommitCount": 2500,
    "topKLabels": 5,
    "trainingRecords": records,
    "dataset": "output/arcan_style_training_dataset.jsonl",
}, indent=2) + "\n", encoding="utf-8")
PY
      fi
      rm -rf "$cache_lock"
      cache_lock=""
      trap - EXIT INT TERM
      TRAINING_DATASET="$cache_dataset"
      require_file "$TRAINING_DATASET"
      [[ -s "$TRAINING_DATASET" ]] || {
        echo "Mining produced an empty training dataset; training will not start." >&2
        echo "Inspect $MINING_CACHE_DIR/output/mining_errors.csv and $LOG_DIR/mining.log" >&2
        exit 1
      }
    fi
    "$PYTHON" - "$TRAINING_DATASET" \
      "$(dirname "$TRAINING_DATASET")/mining_errors.csv" <<'PY'
import csv, sys
from pathlib import Path
rows = sum(1 for line in Path(sys.argv[1]).open(encoding="utf-8") if line.strip())
errors = 0
if Path(sys.argv[2]).is_file():
    with Path(sys.argv[2]).open(newline="", encoding="utf-8-sig") as handle:
        errors = sum(1 for _ in csv.DictReader(handle))
print(f"Validated mining output: {rows:,} training rows, {errors:,} recorded errors")
PY
  else
    echo "Skipped. Use --full to rerun commit mining."
  fi
fi

if should_run training; then
  heading "Stage: training"
  if ((FULL)); then
    TRAINING_DATASET="${TRAINING_DATASET:-$MINING_CACHE_DIR/output/arcan_style_training_dataset.jsonl}"
    require_file "$TRAINING_DATASET"
    RAW_TRAINING_DATASET="$TRAINING_DATASET"
    TRAINING_DATASET="$RUN_ROOT/data/training-dataset.validated.jsonl"
    "$PYTHON" "$PROJECT_ROOT/ml/prepare_training_dataset.py" \
      --input "$RAW_TRAINING_DATASET" \
      --output "$TRAINING_DATASET" \
      --report "$RESULTS_DIR/training-data-quality.json" \
      | tee "$LOG_DIR/training-data-quality.log"
    TRAIN_NOTEBOOK="$RUN_ROOT/notebooks/training.parameterized.ipynb"
    parameterize_notebook \
      "$PROJECT_ROOT/DISTILBERT_IMPROVED_DATA_TRAINED_MODEL.ipynb" \
      "$TRAIN_NOTEBOOK" \
      'C:\dsarp_outputs\improved_dataset\improved_multimetric_arch_smell_dataset.jsonl' \
      "$TRAINING_DATASET" \
      'C:\dsarp_outputs\models\distilbert_improved_strict_ranked_top5' \
      "$MODEL_DIR"
    "$JUPYTER" nbconvert --execute --to notebook \
      --ExecutePreprocessor.timeout=-1 \
      --output "$RUN_ROOT/notebooks/training.executed.ipynb" \
      "$TRAIN_NOTEBOOK" | tee "$LOG_DIR/training.log"
    require_file "$MODEL_DIR/test_set_prediction_comparison.csv"
    "$PYTHON" "$PROJECT_ROOT/ml/evaluate_rankings.py" \
      --comparison-csv "$MODEL_DIR/test_set_prediction_comparison.csv" \
      --output "$RESULTS_DIR/model-evaluation.json" \
      | tee "$LOG_DIR/model-evaluation.log"
  else
    echo "Skipped. Existing predictions will be used."
  fi
fi

if should_run prediction; then
  heading "Stage: prediction"
  if ((FULL)); then
    PREDICTIONS="$RUN_ROOT/pipeline-results/${PROJECT_NAME}_refactoring_suggestions_from_trained_model.csv"
    mkdir -p "$(dirname "$PREDICTIONS")"
    "$PYTHON" "$PROJECT_ROOT/ml/predict_refactorings.py" \
      --model-inputs "$MODEL_INPUTS" \
      --model-dir "$MODEL_DIR/final_model" \
      --output "$PREDICTIONS" \
      | tee "$LOG_DIR/prediction.log"
  else
    echo "Using retained predictions: $PREDICTIONS"
  fi
  require_file "$PREDICTIONS"
  "$PYTHON" - "$PREDICTIONS" <<'PY'
import sys
import pandas as pd
frame = pd.read_csv(sys.argv[1])
print(f"Prediction records: {len(frame):,}")
PY
  "$PYTHON" - "$PREDICTIONS" "$PROJECT_NAME" "$VERSION_ID" <<'PY'
import csv, sys
with open(sys.argv[1], newline="", encoding="utf-8-sig") as handle:
    rows = list(csv.DictReader(handle))
versions = {row.get("versionId") for row in rows if row.get("versionId")}
projects = {row.get("project") for row in rows if row.get("project")}
if versions and versions != {sys.argv[3]}:
    raise SystemExit(f"Prediction versionId {sorted(versions)} does not match {sys.argv[3]}")
if projects and projects != {sys.argv[2]}:
    raise SystemExit(f"Prediction project {sorted(projects)} does not match {sys.argv[2]}")
PY
fi

if should_run clone; then
  heading "Stage: clone"
  if [[ -e "$BASE_REPO" ]]; then
    echo "Reusing existing baseline repository: $BASE_REPO"
  else
    git clone "$REPOSITORY_URL" "$BASE_REPO"
    git -C "$BASE_REPO" switch --detach "$VERSION_ID"
  fi
  test "$(git -C "$BASE_REPO" rev-parse HEAD)" = "$VERSION_ID"
  test -z "$(git -C "$BASE_REPO" status --porcelain)" || {
    echo "Existing baseline repository is not clean: $BASE_REPO" >&2
    exit 1
  }
fi

if should_run baseline; then
  heading "Stage: baseline"
  require_file "$BASE_REPO/mvnw"
  run_expected_spring_failure "$BASE_REPO" "$LOG_DIR/baseline-verify.log"
  "$PYTHON" "$PROJECT_ROOT/evaluation/summarize_arcan.py" baseline-csv \
    "$BASELINE_CSV_DIR" --output "$RESULTS_DIR/arcan-supplied-baseline-summary.json" >/dev/null
  "$ARCAN_RUNNER" \
    --repository "$BASE_REPO" \
    --output-dir "$RESULTS_DIR/arcan-baseline-matched" \
    --java-home "$JAVA_HOME_17" \
    --arcan-home "$ARCAN_HOME"
fi

if should_run rewrite; then
  heading "Stage: OpenRewrite"
  if [[ ! -e "$REWRITE_REPO" ]]; then
    git -C "$BASE_REPO" worktree add \
      -b "experiment/openrewrite-$PROJECT_NAME" "$REWRITE_REPO" "$VERSION_ID"
  fi

  "$OPENREWRITE_GENERATOR" \
    --repository "$REWRITE_REPO" \
    --predictions "$PREDICTIONS" \
    --output-dir "$RESULTS_DIR/generated-openrewrite" \
    --severity-categories "$SEVERITY_CATEGORIES"

  validator_arguments=(
    --repository "$REWRITE_REPO"
    --generated-dir "$RESULTS_DIR/generated-openrewrite"
    --output-dir "$RESULTS_DIR/openrewrite-validation"
    --rewrite-runner "$OPENREWRITE_RUNNER"
    --java-home "$JAVA_HOME_17"
    --compatibility-profile "$VALIDATOR_COMPATIBILITY_PROFILE"
    --batch-size "$BATCH_SIZE"
    --start-batch "$START_BATCH"
    --max-batches "$MAX_BATCHES"
  )
  if ((ALLOW_RISKY_CANDIDATES)); then
    validator_arguments+=(--allow-risky-candidates)
  fi
  "$PYTHON" "$CANDIDATE_VALIDATOR" "${validator_arguments[@]}"

  validated_count="$(jq -r '.validated_count' "$RESULTS_DIR/openrewrite-validation/validation-report.json")"
  if ((validated_count > 0)); then
    "$OPENREWRITE_RUNNER" \
      --repository "$REWRITE_REPO" \
      --java-home "$JAVA_HOME_17" \
      --recipe "$RESULTS_DIR/openrewrite-validation/validated-candidates.yml" \
      --active-recipe generated.architecture.ApplyValidatedCandidates \
      --results-dir "$RESULTS_DIR" \
      --log-dir "$LOG_DIR" \
      --mode all
  else
    echo "No candidate passed isolated source/build/API validation; leaving the worktree unchanged."
  fi
fi

if should_run focused_test; then
  heading "Stage: focused tests"
  if [[ "$PROFILE" == "log4j2" ]]; then
    (cd "$REWRITE_REPO" && JAVA_HOME="$JAVA_HOME_17" ./mvnw \
      -pl log4j-core-test -am \
      -Dtest=org.apache.logging.log4j.core.appender.rolling.FileSizeTest,org.apache.logging.log4j.core.appender.rolling.action.FileSizeTest \
      -Dsurefire.failIfNoSpecifiedTests=false test) | tee "$LOG_DIR/focused-tests.log"
  else
    echo "No project-specific focused test configured; isolated and full verification remain enabled."
  fi
fi

if should_run format; then
  heading "Stage: format OpenRewrite changes"
  if [[ "$PROFILE" == "log4j2" ]]; then
    (cd "$REWRITE_REPO" && JAVA_HOME="$JAVA_HOME_17" ./mvnw \
      -pl log4j-api,log4j-core,log4j-1.2-api -am \
      -DskipTests spotless:apply) | tee "$LOG_DIR/spotless-apply.log"
  elif rg -q 'spotless-maven-plugin' "$REWRITE_REPO" -g 'pom.xml'; then
    (cd "$REWRITE_REPO" && JAVA_HOME="$JAVA_HOME_17" ./mvnw \
      -DskipTests spotless:apply) | tee "$LOG_DIR/spotless-apply.log"
  else
    echo "Spotless is not configured; formatting stage skipped."
  fi
  git -C "$REWRITE_REPO" diff --check
fi

if should_run final_verify; then
  heading "Stage: final verification"
  run_expected_spring_failure "$REWRITE_REPO" "$LOG_DIR/refactored-verify.log"
fi

if should_run smells; then
  heading "Stage: smell comparison"
  "$ARCAN_RUNNER" \
    --repository "$REWRITE_REPO" \
    --output-dir "$RESULTS_DIR/arcan-refactored" \
    --java-home "$JAVA_HOME_17" \
    --arcan-home "$ARCAN_HOME"
  "$PYTHON" "$PROJECT_ROOT/evaluation/summarize_arcan.py" compare \
    "$RESULTS_DIR/arcan-baseline-matched/summary.json" \
    "$RESULTS_DIR/arcan-refactored/summary.json" \
    --output "$RESULTS_DIR/arcan-comparison.json" >/dev/null
  jq '.metrics | with_entries(select(.key != "package_cycle_sets"))' \
    "$RESULTS_DIR/arcan-comparison.json" \
    | tee "$RESULTS_DIR/arcan-comparison.txt"

  "$PYTHON" "$PROJECT_ROOT/evaluation/build_experiment_report.py" \
    --repository "$REWRITE_REPO" \
    --version-id "$VERSION_ID" \
    --predictions "$PREDICTIONS" \
    --manifest "$RESULTS_DIR/generated-openrewrite/manifest.json" \
    --validation "$RESULTS_DIR/openrewrite-validation/validation-report.json" \
    --comparison "$RESULTS_DIR/arcan-comparison.json" \
    --model-evaluation "$RESULTS_DIR/model-evaluation.json" \
    --training-data-quality "$RESULTS_DIR/training-data-quality.json" \
    --provenance "$RESULTS_DIR/run-provenance.json" \
    --output "$RESULTS_DIR/experiment-report.json" >/dev/null
fi

if should_run summary; then
  heading "Stage: summary"
  echo "Run root:       $RUN_ROOT"
  echo "Project:        $PROJECT_NAME"
  echo "Repository URL: $REPOSITORY_URL"
  echo "Version:        $VERSION_ID"
  echo "Baseline repo:  $BASE_REPO"
  echo "Rewrite repo:   $REWRITE_REPO"
  echo "Results:        $RESULTS_DIR"
  echo "Logs:           $LOG_DIR"
  echo "Baseline CSVs:  $BASELINE_CSV_DIR"
  echo
  git --no-pager -C "$REWRITE_REPO" status --short
  git --no-pager -C "$REWRITE_REPO" diff --stat
  echo
  echo "No commit was created. Review the worktree and result files."
fi
