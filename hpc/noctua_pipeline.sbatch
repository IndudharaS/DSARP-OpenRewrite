#!/usr/bin/env bash
#SBATCH --job-name=dsarp-pipeline
#SBATCH --account=hpc-prf-dssecs
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=1-00:00:00
#SBATCH --output=slurm-pipeline-%j.out
#SBATCH --error=slurm-pipeline-%j.err

set -Eeuo pipefail

PROJECT_SPACE="${PROJECT_SPACE:-/scratch/hpc-prf-dssecs/$USER}"
PROJECT_ROOT="${PROJECT_ROOT:-$PROJECT_SPACE/dsarp-openrewrite}"
VENV_PATH="${VENV_PATH:-$PROJECT_SPACE/environments/dsarp-python-3.12}"
TOOLS_ROOT="${TOOLS_ROOT:-$PROJECT_SPACE/tools}"

# The virtual environment links against libraries supplied by the PC² Python
# module (including libffi); loading the module is required in every fresh job.
module reset
module load lang
module load Python/3.12.3-GCCcore-13.3.0

export DSARP_JAVA_HOME_17="/opt/software/pc2/EB-SW/software/Java/17.0.6"
export DSARP_JAVA_HOME_21="/opt/software/pc2/EB-SW/software/Java/21.0.2"
export DSARP_PYTHON="$VENV_PATH/bin/python"
export DSARP_JUPYTER="$VENV_PATH/bin/jupyter"
export DSARP_REFACTORING_MINER="$TOOLS_ROOT/refactoring-miner/RefactoringMiner-3.1.4/bin/RefactoringMiner"
export DSARP_ARCAN_HOME="$TOOLS_ROOT/arcan-1.2.1/distribution/arcan-1.2.1"
# A job-private Maven repository prevents two dashboard submissions from
# concurrently replacing the same artifact on the parallel filesystem. Such a
# collision previously surfaced as ClassFormatError: Truncated class file.
MAVEN_REPOSITORY="${MAVEN_REPOSITORY:-$PROJECT_SPACE/maven-repository/jobs/$SLURM_JOB_ID}"
mkdir -p "$MAVEN_REPOSITORY"
export MAVEN_OPTS="-Dmaven.repo.local=$MAVEN_REPOSITORY -Xmx12g"
# Noctua scratch enforces project-group/setgid semantics, so Log4j's test that
# asserts arbitrary owner/group/mode changes is not valid on this filesystem.
# The pipeline passes this through Surefire's additive excludes file so the
# project's execution-specific exclusions (including OverflowTest) stay intact.
export DSARP_MAVEN_TEST_EXCLUDES="**/FileAppenderPermissionsTest.*"
# Several Log4j 1.x compatibility tests write the same target/temp.A* files.
# A single Surefire fork prevents cross-process races on high-core-count nodes.
export DSARP_MAVEN_FORK_COUNT="1"

SYSTEM="${SYSTEM:-logging-log4j2}"
REPOSITORY_URL="${REPOSITORY_URL:-https://github.com/apache/logging-log4j2.git}"
VERSION_ID="${VERSION_ID:-4f474b32751f4ccad67424ca585612584440cd63}"
PROFILE="${PROFILE:-}"
BASELINE_CSV_DIR="${BASELINE_CSV_DIR:-$PROJECT_ROOT/baseline_csv}"
SEVERITY_CATEGORIES="${SEVERITY_CATEGORIES:-high}"
BATCH_SIZE="${BATCH_SIZE:-10}"
START_BATCH="${START_BATCH:-1}"
MAX_BATCHES="${MAX_BATCHES:-1}"
PIPELINE_MODE="${PIPELINE_MODE:-reuse_predictions}"
MAX_COMMITS_PER_REPO="${MAX_COMMITS_PER_REPO:-500}"
PREDICTIONS="${PREDICTIONS_CSV:-$PROJECT_ROOT/shared/pipeline-cache/$SYSTEM/${VERSION_ID,,}/predictions.csv}"
TRAINING_DATASET="${TRAINING_DATASET:-}"
PRETRAINED_MODEL_DIR="${PRETRAINED_MODEL_DIR:-$PROJECT_ROOT/shared/trained-model/default/final_model}"
RUN_ID="${RESUME_RUN_ID:-$SLURM_JOB_ID}"
RUN_ROOT="${RUN_ROOT:-$PROJECT_SPACE/runs/$SYSTEM/$RUN_ID}"

required=(
  "$DSARP_PYTHON"
  "$DSARP_JAVA_HOME_17/bin/java"
  "$DSARP_JAVA_HOME_21/bin/java"
  "$DSARP_REFACTORING_MINER"
  "$DSARP_ARCAN_HOME/arcan-1.2.1.jar"
  "$BASELINE_CSV_DIR/component-metrics.csv"
  "$BASELINE_CSV_DIR/smell-characteristics.csv"
  "$BASELINE_CSV_DIR/smell-affects.csv"
)
if [[ "$PIPELINE_MODE" == "reuse_predictions" ]]; then
  required+=("$PREDICTIONS")
elif [[ "$PIPELINE_MODE" == "training" ]]; then
  required+=("$TRAINING_DATASET")
elif [[ "$PIPELINE_MODE" == "pretrained_model" ]]; then
  required+=("$PRETRAINED_MODEL_DIR/config.json" "$PRETRAINED_MODEL_DIR/labels.json")
elif [[ "$PIPELINE_MODE" != "fresh" && "$PIPELINE_MODE" != "train" ]]; then
  echo "PIPELINE_MODE must be fresh, train, training, pretrained_model or reuse_predictions: $PIPELINE_MODE" >&2
  exit 2
fi
for path in "${required[@]}"; do
  [[ -e "$path" ]] || { echo "Required HPC input missing: $path" >&2; exit 1; }
done

"$DSARP_PYTHON" -c \
  "import accelerate, torch, transformers, datasets, pandas, sklearn; print('Python runtime dependencies OK'); print('Accelerate:', accelerate.__version__)"

mkdir -p "$RUN_ROOT" "$MAVEN_REPOSITORY"
cd "$PROJECT_ROOT"

echo "Job: $SLURM_JOB_ID on $(hostname)"
echo "Severity: $SEVERITY_CATEGORIES; batch size: $BATCH_SIZE; start: $START_BATCH; count: $MAX_BATCHES"
echo "Pipeline mode: $PIPELINE_MODE"
echo "Maximum commits per training repository: $MAX_COMMITS_PER_REPO"
echo "System: $SYSTEM"
echo "Repository: $REPOSITORY_URL"
echo "Version: $VERSION_ID"
echo "Run root: $RUN_ROOT"
echo "Maven repository: $MAVEN_REPOSITORY"

pipeline_resume_arguments=()
if [[ -n "${START_STAGE:-}" ]]; then
  [[ -n "${RESUME_RUN_ID:-}" ]] || {
    echo "START_STAGE requires RESUME_RUN_ID to avoid resuming an empty run." >&2
    exit 2
  }
  pipeline_resume_arguments+=(--from "$START_STAGE")
  printf 'Slurm job %s resumed run %s from %s with Maven fork count %s\n' \
    "$SLURM_JOB_ID" "$RESUME_RUN_ID" "$START_STAGE" "$DSARP_MAVEN_FORK_COUNT" \
    >"$RUN_ROOT/resume-$SLURM_JOB_ID.txt"
  echo "Resuming run $RESUME_RUN_ID from stage $START_STAGE"
fi
if [[ -n "${STOP_STAGE:-}" ]]; then
  pipeline_resume_arguments+=(--through "$STOP_STAGE")
  echo "Pipeline will run through stage $STOP_STAGE"
fi

pipeline_input_arguments=()
if [[ "$PIPELINE_MODE" == "fresh" ]]; then
  pipeline_input_arguments+=(--remine)
elif [[ "$PIPELINE_MODE" == "reuse_predictions" ]]; then
  pipeline_input_arguments+=(--predictions-csv "$PREDICTIONS")
elif [[ "$PIPELINE_MODE" == "training" ]]; then
  pipeline_input_arguments+=(--training-dataset "$TRAINING_DATASET")
elif [[ "$PIPELINE_MODE" == "pretrained_model" ]]; then
  pipeline_input_arguments+=(--pretrained-model-dir "$PRETRAINED_MODEL_DIR")
fi

pipeline_policy_arguments=()
if [[ "${ALLOW_RISKY_CANDIDATES:-0}" == "1" ]]; then
  pipeline_policy_arguments+=(--allow-risky-candidates)
fi
if [[ -n "$PROFILE" ]]; then
  pipeline_policy_arguments+=(--profile "$PROFILE")
fi

scripts/run_generic_pipeline.sh \
  --system "$SYSTEM" \
  --repository-url "$REPOSITORY_URL" \
  --version-id "$VERSION_ID" \
  --baseline-csv-dir "$BASELINE_CSV_DIR" \
  --run-root "$RUN_ROOT" \
  --severity-categories "$SEVERITY_CATEGORIES" \
  --batch-size "$BATCH_SIZE" \
  --start-batch "$START_BATCH" \
  --max-batches "$MAX_BATCHES" \
  --max-commits-per-repo "$MAX_COMMITS_PER_REPO" \
  "${pipeline_policy_arguments[@]}" \
  "${pipeline_input_arguments[@]}" \
  "${pipeline_resume_arguments[@]}"

echo "Pipeline completed. Results: $RUN_ROOT/results"
