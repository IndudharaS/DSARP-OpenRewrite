#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PROJECT_NAME=""
REPOSITORY_URL=""
VERSION_ID=""
BASELINE_CSV_DIR="$PROJECT_ROOT/baseline_csv"
PREDICTIONS=""
TRAINING_DATASET=""
MINING_CACHE_DIR=""
REMINE=0
REUSE_PREDICTIONS=0
FULL=1
FORWARD=()
RUN_ROOT=""
PROFILE=""
ALLOW_RISKY_CANDIDATES=1

usage() {
  cat <<'EOF'
Usage: scripts/run_generic_pipeline.sh --system NAME --repository-url URL \
  --version-id COMMIT [options]

Required:
  --system NAME             Stable system/project name used in CSV records.
  --repository-url URL      Git URL for the target Maven repository.
  --version-id COMMIT       Exact target revision.

Inputs:
  --baseline-csv-dir PATH   Folder containing component-metrics.csv,
                            smell-characteristics.csv and smell-affects.csv.
  --predictions-csv PATH    Reuse model predictions and skip mining/training.
  --reuse-predictions       Reuse predictions cached for this system+commit.
  --training-dataset PATH   Reuse mined JSONL records but retrain the model.
  --mining-cache-dir PATH   Override the shared RefactoringMiner cache folder.
  --remine                  Generate fresh shared mining output.
  --profile PROFILE         generic or log4j2. Defaults to log4j2 for the
                            logging-log4j2 preset and generic otherwise.
  --allow-risky-candidates  Execute public-API candidates in isolated
                            worktrees; Maven validation is still required.
                            This is the default.
  --skip-risky-candidates   Route public-API candidates to manual_review
                            instead of validating them (the old conservative
                            default).

Lifecycle:
  --clean                   Back up an existing run before starting.
  --from STAGE              Resume from a pipeline stage.
  --through STAGE           Stop after a pipeline stage.
  --run-root PATH           Override the isolated run directory.

Without --predictions-csv, mining, training and prediction run from the start.
Results are written below runs/NAME/results.
EOF
}

while (($#)); do
  case "$1" in
    --system) PROJECT_NAME="${2:?missing name}"; shift 2 ;;
    --repository-url) REPOSITORY_URL="${2:?missing URL}"; shift 2 ;;
    --version-id) VERSION_ID="${2:?missing commit}"; shift 2 ;;
    --baseline-csv-dir) BASELINE_CSV_DIR="${2:?missing path}"; shift 2 ;;
    --predictions-csv) PREDICTIONS="${2:?missing path}"; FULL=0; shift 2 ;;
    --reuse-predictions) REUSE_PREDICTIONS=1; FULL=0; shift ;;
    --training-dataset) TRAINING_DATASET="${2:?missing path}"; FULL=1; shift 2 ;;
    --mining-cache-dir) MINING_CACHE_DIR="${2:?missing path}"; shift 2 ;;
    --remine) REMINE=1; shift ;;
    --profile) PROFILE="${2:?missing profile}"; shift 2 ;;
    --allow-risky-candidates) ALLOW_RISKY_CANDIDATES=1; shift ;;
    --skip-risky-candidates) ALLOW_RISKY_CANDIDATES=0; shift ;;
    --run-root) RUN_ROOT="${2:?missing path}"; shift 2 ;;
    --clean|--from|--through)
      FORWARD+=("$1")
      if [[ "$1" != "--clean" ]]; then FORWARD+=("${2:?missing stage}"); shift; fi
      shift
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$PROJECT_NAME" && -n "$REPOSITORY_URL" && -n "$VERSION_ID" ]] || { usage >&2; exit 2; }
[[ "$PROJECT_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "Invalid system name: $PROJECT_NAME" >&2; exit 2; }
if [[ -z "$PROFILE" ]]; then
  if [[ "$PROJECT_NAME" == "logging-log4j2" ]]; then PROFILE="log4j2"; else PROFILE="generic"; fi
fi
[[ "$PROFILE" =~ ^(generic|log4j2)$ ]] || { echo "Invalid profile: $PROFILE" >&2; exit 2; }
if ((REUSE_PREDICTIONS)); then
  VERSION_KEY="$(printf '%s' "$VERSION_ID" | tr '[:upper:]' '[:lower:]')"
  PREDICTIONS="$PROJECT_ROOT/shared/pipeline-cache/$PROJECT_NAME/$VERSION_KEY/predictions.csv"
  [[ -f "$PREDICTIONS" ]] || {
    echo "No shared predictions for $PROJECT_NAME at $VERSION_ID: $PREDICTIONS" >&2
    exit 2
  }
fi

EFFECTIVE_RUN_ROOT="${RUN_ROOT:-$PROJECT_ROOT/runs/$PROJECT_NAME}"
arguments=(
  --profile "$PROFILE"
  --project-name "$PROJECT_NAME"
  --repository-url "$REPOSITORY_URL"
  --version-id "$VERSION_ID"
  --baseline-csv-dir "$BASELINE_CSV_DIR"
  --run-root "$EFFECTIVE_RUN_ROOT"
  --model-inputs "$EFFECTIVE_RUN_ROOT/pipeline-results/${PROJECT_NAME}_model_inputs.csv"
)
if ((FULL)); then
  arguments+=(--full)
  if [[ -n "$MINING_CACHE_DIR" ]]; then
    arguments+=(--mining-cache-dir "$MINING_CACHE_DIR")
  fi
  if ((REMINE)); then
    arguments+=(--remine)
  fi
  if [[ -n "$TRAINING_DATASET" ]]; then
    arguments+=(--training-dataset "$TRAINING_DATASET")
  fi
else
  arguments+=(--predictions-csv "$PREDICTIONS")
fi
if ((ALLOW_RISKY_CANDIDATES)); then
  arguments+=(--allow-risky-candidates)
else
  arguments+=(--skip-risky-candidates)
fi

if ((${#FORWARD[@]})); then
  exec "$SCRIPT_DIR/run_log4j2_pipeline.sh" "${arguments[@]}" "${FORWARD[@]}"
else
  exec "$SCRIPT_DIR/run_log4j2_pipeline.sh" "${arguments[@]}"
fi
