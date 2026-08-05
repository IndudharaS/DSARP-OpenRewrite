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

PROJECT_SPACE="/scratch/hpc-prf-dssecs/$USER"
PROJECT_ROOT="$PROJECT_SPACE/dsarp-openrewrite"
VENV_PATH="$PROJECT_SPACE/environments/dsarp-python-3.12"
TOOLS_ROOT="$PROJECT_SPACE/tools"

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
export MAVEN_OPTS="-Dmaven.repo.local=$PROJECT_SPACE/maven-repository -Xmx12g"
# Noctua scratch enforces project-group/setgid semantics, so Log4j's test that
# asserts arbitrary owner/group/mode changes is not valid on this filesystem.
# All other tests run for both baseline and refactored verification.
export DSARP_MAVEN_TEST_PATTERN="*,!org.apache.logging.log4j.core.appender.FileAppenderPermissionsTest"

SEVERITY_CATEGORIES="${SEVERITY_CATEGORIES:-high}"
BATCH_SIZE="${BATCH_SIZE:-10}"
START_BATCH="${START_BATCH:-1}"
MAX_BATCHES="${MAX_BATCHES:-1}"
VERSION_ID="4f474b32751f4ccad67424ca585612584440cd63"
PREDICTIONS="$PROJECT_ROOT/shared/pipeline-cache/logging-log4j2/$VERSION_ID/predictions.csv"
RUN_ROOT="$PROJECT_SPACE/runs/logging-log4j2/${SLURM_JOB_ID}"

required=(
  "$DSARP_PYTHON"
  "$DSARP_JAVA_HOME_17/bin/java"
  "$DSARP_JAVA_HOME_21/bin/java"
  "$DSARP_REFACTORING_MINER"
  "$DSARP_ARCAN_HOME/arcan-1.2.1.jar"
  "$PREDICTIONS"
)
for path in "${required[@]}"; do
  [[ -e "$path" ]] || { echo "Required HPC input missing: $path" >&2; exit 1; }
done

"$DSARP_PYTHON" -c \
  "import torch, transformers, datasets, pandas, sklearn; print('Python runtime dependencies OK')"

mkdir -p "$RUN_ROOT" "$PROJECT_SPACE/maven-repository"
cd "$PROJECT_ROOT"

echo "Job: $SLURM_JOB_ID on $(hostname)"
echo "Severity: $SEVERITY_CATEGORIES; batch size: $BATCH_SIZE; start: $START_BATCH; count: $MAX_BATCHES"
echo "Run root: $RUN_ROOT"

scripts/run_generic_pipeline.sh \
  --system logging-log4j2 \
  --repository-url https://github.com/apache/logging-log4j2.git \
  --version-id "$VERSION_ID" \
  --baseline-csv-dir "$PROJECT_ROOT/baseline_csv" \
  --predictions-csv "$PREDICTIONS" \
  --run-root "$RUN_ROOT" \
  --severity-categories "$SEVERITY_CATEGORIES" \
  --batch-size "$BATCH_SIZE" \
  --start-batch "$START_BATCH" \
  --max-batches "$MAX_BATCHES" \
  --allow-risky-candidates

echo "Pipeline completed. Results: $RUN_ROOT/results"
