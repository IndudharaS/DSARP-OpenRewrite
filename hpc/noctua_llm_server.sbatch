#!/usr/bin/env bash
#SBATCH --job-name=dsarp-llm
#SBATCH --account=hpc-prf-dssecs
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --gres=gpu:a100:2
#SBATCH --mem=128G
#SBATCH --time=7-00:00:00
#SBATCH --output=slurm-llm-%j.out
#SBATCH --error=slurm-llm-%j.err

set -Eeuo pipefail

PROJECT_SPACE="${PROJECT_SPACE:-/scratch/hpc-prf-dssecs/$USER}"
VLLM_ENV="${VLLM_ENV:-$PROJECT_SPACE/environments/dsarp-vllm}"
MODEL="${LLM_MODEL:-Qwen/Qwen3-Coder-30B-A3B-Instruct}"
PORT="${LLM_PORT:-8000}"
SERVICE_DIR="${LLM_SERVICE_DIR:-$PROJECT_SPACE/llm-services/$SLURM_JOB_ID}"
MODEL_CACHE="${HF_HOME:-$PROJECT_SPACE/huggingface-cache}"

module reset
module load lang
module load Python/3.12.3-GCCcore-13.3.0

[[ -x "$VLLM_ENV/bin/python" ]] || {
  echo "Missing vLLM environment: $VLLM_ENV" >&2
  echo "Create it once with a GPU-compatible PyTorch and vLLM installation." >&2
  exit 1
}

mkdir -p "$SERVICE_DIR" "$MODEL_CACHE"
export HF_HOME="$MODEL_CACHE"
export PYTHONUNBUFFERED=1
node="$(hostname -f 2>/dev/null || hostname)"
endpoint="http://$node:$PORT"

"$VLLM_ENV/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --served-model-name "$MODEL" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --dtype bfloat16 \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 32768 \
  >"$SERVICE_DIR/server.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 180); do
  if curl -fsS --connect-timeout 2 --max-time 5 "http://127.0.0.1:$PORT/v1/models" >/dev/null; then
    "$VLLM_ENV/bin/python" - "$SERVICE_DIR/service.json" "$endpoint" "$MODEL" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "status": "ready", "endpoint": sys.argv[2], "model": sys.argv[3],
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "node": os.environ.get("SLURMD_NODENAME"),
    "created_at": datetime.now(timezone.utc).isoformat(),
}, indent=2) + "\n", encoding="utf-8")
PY
    echo "LLM service ready: $endpoint"
    echo "Service metadata: $SERVICE_DIR/service.json"
    wait "$server_pid"
    exit $?
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    wait "$server_pid"
    exit $?
  fi
  sleep 5
done

echo "LLM service did not become healthy within 15 minutes; see $SERVICE_DIR/server.log" >&2
exit 1
