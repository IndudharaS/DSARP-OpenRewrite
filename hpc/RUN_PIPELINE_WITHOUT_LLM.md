# Run the pipeline on Noctua HPC without an LLM

This guide runs the original deterministic pipeline on the `normal` CPU
partition. It does not submit a GPU job, start vLLM, or call an external API.

## 1. Prepare a clean shell

```bash
cd /scratch/hpc-prf-dssecs/$USER/dsarp-openrewrite
git pull origin HCP-Control

unset LLM_ENDPOINT
unset DSARP_LLM_ENDPOINT
```

No `dsarp-llm` job is required. GPU partition availability does not affect this
workflow.

## 2. Recommended: submit through the dashboard

Start or restart the persistent dashboard controller:

```bash
hpc/manage_dashboard.sh restart
hpc/manage_dashboard.sh status
```

On the local computer, create the SSH tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 swamyv@n2login1
```

Open <http://127.0.0.1:8765>, then configure the experiment:

1. Select the software system and exact commit.
2. Select **Noctua HPC (Slurm)**.
3. Select **Start a new HPC run**.
4. Choose **Fast verified flow** to reuse exact-commit predictions, or
   **Complete flow from shared mining** to train and predict again.
5. Under **Recipe resolution method**, choose **Normal deterministic flow**.
6. Select the required severities.
7. For the first check, use 10 candidates per batch, start at batch 1, and run
   1 batch.
8. Upload the three baseline Arcan CSV files belonging to this exact project
   and revision.
9. Start the experiment.

In normal deterministic mode, the dashboard exports an empty `LLM_ENDPOINT`;
the pipeline therefore uses only deterministic semantic and
import-based candidate resolution.

## 3. Direct Slurm submission using cached predictions

Use this when the exact system and commit already have a compatible prediction
CSV under `shared/pipeline-cache`.

Set the target. This example is Apache Tika:

```bash
export SYSTEM=tika
export REPOSITORY_URL=https://github.com/apache/tika.git
export VERSION_ID=697d7c047daf1f661a4ed067bbc8f9c58bb6faa2
export BASELINE_CSV_DIR=/absolute/path/to/tika-baseline-csv

export PIPELINE_MODE=reuse_predictions
export SEVERITY_CATEGORIES=high,medium,low
export BATCH_SIZE=10
export START_BATCH=1
export MAX_BATCHES=1
export LLM_ENDPOINT=
```

The baseline directory must contain:

```text
component-metrics.csv
smell-characteristics.csv
smell-affects.csv
```

Confirm that cached predictions exist:

```bash
test -f "shared/pipeline-cache/$SYSTEM/${VERSION_ID,,}/predictions.csv" \
  && echo "Cached predictions found" \
  || echo "Cached predictions are missing"
```

Submit the CPU pipeline:

```bash
PIPELINE_JOB=$(sbatch --parsable --export=ALL hpc/noctua_pipeline.sbatch)
echo "Pipeline job: $PIPELINE_JOB"
```

## 4. Direct Slurm submission for Log4j2

```bash
export SYSTEM=logging-log4j2
export REPOSITORY_URL=https://github.com/apache/logging-log4j2.git
export VERSION_ID=4f474b32751f4ccad67424ca585612584440cd63
export BASELINE_CSV_DIR=/absolute/path/to/log4j2-baseline-csv

export PIPELINE_MODE=reuse_predictions
export SEVERITY_CATEGORIES=high,medium,low
export BATCH_SIZE=10
export START_BATCH=1
export MAX_BATCHES=1
export LLM_ENDPOINT=

PIPELINE_JOB=$(sbatch --parsable --export=ALL hpc/noctua_pipeline.sbatch)
echo "Pipeline job: $PIPELINE_JOB"
```

The Log4j2 compatibility profile and curated FileSize candidate are selected
automatically by the dashboard. For a direct Slurm submission, enable the
curated candidate explicitly if required:

```bash
export INCLUDE_CURATED_FILESIZE=1
```

Otherwise set it to zero:

```bash
export INCLUDE_CURATED_FILESIZE=0
```

## 5. Retrain and generate predictions without fresh mining

This reuses the shared RefactoringMiner dataset, retrains the model, generates
new target predictions, and continues through validation and Arcan:

```bash
export PIPELINE_MODE=train
export LLM_ENDPOINT=
export SEVERITY_CATEGORIES=high,medium,low
export BATCH_SIZE=10
export START_BATCH=1
export MAX_BATCHES=1

PIPELINE_JOB=$(sbatch --parsable --export=ALL hpc/noctua_pipeline.sbatch)
echo "Pipeline job: $PIPELINE_JOB"
```

This is slower than `reuse_predictions`, but much faster than mining all source
histories again.

## 6. Complete fresh run

Use fresh mode only when historical mining must be repeated:

```bash
export PIPELINE_MODE=fresh
export MAX_COMMITS_PER_REPO=2000
export LLM_ENDPOINT=
export SEVERITY_CATEGORIES=high,medium,low
export BATCH_SIZE=10
export START_BATCH=1
export MAX_BATCHES=1

PIPELINE_JOB=$(sbatch --parsable --export=ALL hpc/noctua_pipeline.sbatch)
echo "Pipeline job: $PIPELINE_JOB"
```

Fresh mode performs mining, training and prediction before repository
validation, so it can take many hours.

## 7. Monitor the job

```bash
squeue -j "$PIPELINE_JOB" \
  -o "%.18i %.20j %.9T %.10M %.6D %.6C %.10m %R"
```

After the job begins, follow its log:

```bash
tail -f "slurm-pipeline-$PIPELINE_JOB.out"
```

Press `Ctrl+C` to stop following the log; the Slurm job continues.

Inspect final accounting:

```bash
sacct -j "$PIPELINE_JOB" \
  --format=JobID,JobName,Partition,State,Elapsed,MaxRSS,AllocCPUS,ExitCode
```

Results are written to:

```text
/scratch/hpc-prf-dssecs/$USER/runs/<system>/<job-id>/results
```

## 8. Confirm that no LLM was used

```bash
RUN=/scratch/hpc-prf-dssecs/$USER/runs/$SYSTEM/$PIPELINE_JOB

grep -n "LLM candidate resolution" "slurm-pipeline-$PIPELINE_JOB.out" \
  || echo "No LLM resolution was enabled"
```

If a generated manifest is available:

```bash
jq '.llm_resolution // {enabled: false}' \
  "$RUN/results/generated-openrewrite/manifest.json"
```

The expected value is `"enabled": false`.

## 9. Clean the exported settings afterward

```bash
unset SYSTEM REPOSITORY_URL VERSION_ID BASELINE_CSV_DIR
unset PIPELINE_MODE SEVERITY_CATEGORIES BATCH_SIZE START_BATCH MAX_BATCHES
unset MAX_COMMITS_PER_REPO INCLUDE_CURATED_FILESIZE LLM_ENDPOINT
```
