#!/usr/bin/env bash
#SBATCH --job-name=dsarp-setup
#SBATCH --account=hpc-prf-dssecs
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=slurm-setup-%j.out
#SBATCH --error=slurm-setup-%j.err

set -Eeuo pipefail

PROJECT_SPACE="/scratch/hpc-prf-dssecs/$USER"
PROJECT_ROOT="$PROJECT_SPACE/dsarp-openrewrite"
VENV_PATH="$PROJECT_SPACE/environments/dsarp-python-3.12"

module reset
module load lang
module load Python/3.12.3-GCCcore-13.3.0

mkdir -p "$PROJECT_SPACE/environments" "$PROJECT_SPACE/maven-repository"
if [[ ! -x "$VENV_PATH/bin/python" ]]; then
  python3 -m venv "$VENV_PATH"
fi

source "$VENV_PATH/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install --requirement "$PROJECT_ROOT/requirements.lock"
python -c \
  "import torch, transformers, datasets, pandas, sklearn; print('Python runtime dependencies OK')"

export DSARP_PYTHON="$VENV_PATH/bin/python"
export DSARP_JUPYTER="$VENV_PATH/bin/jupyter"
cd "$PROJECT_ROOT"
scripts/check_project.sh

echo "Noctua Python environment is ready: $VENV_PATH"
