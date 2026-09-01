# Noctua 2 execution

## Web interface (recommended)

Run the lightweight persistent dashboard controller on the login node; it
submits heavy work to Slurm and remains available after an SSH/VS Code terminal
disconnects:

```bash
cd /scratch/hpc-prf-dssecs/$USER/dsarp-openrewrite
hpc/manage_dashboard.sh start
hpc/manage_dashboard.sh status
```

From the client, forward the dashboard with
`ssh -N -L 8765:127.0.0.1:8765 n2login1`, then open
`http://127.0.0.1:8765`. The interface can submit, monitor, cancel and resume
Slurm runs. Use `hpc/manage_dashboard.sh logs` for server output and
`hpc/manage_dashboard.sh restart` after pulling dashboard code. Stopping this
server does not cancel Slurm jobs. Full instructions are in `webui/README.md`.

These scripts run the pipeline on PC² Noctua 2 under account
`hpc-prf-dssecs`. Compute-heavy setup and experiments must be submitted through
Slurm, not executed on a login node.

The PC² Python module must be loaded whenever the scratch virtual environment
is used because compiled extensions depend on module-provided shared libraries
such as `libffi.so.8`. Both Slurm scripts load it automatically.

Noctua's project scratch filesystem also enforces project-group/setgid behavior.
The pipeline therefore excludes only Log4j's `FileAppenderPermissionsTest`,
which asserts that a process can assign arbitrary owner, group, and mode values.
The exclusion is applied identically to baseline and refactored verification and
is recorded in `run-provenance.json`. It uses Surefire's additive exclusions file,
so the project's normal test-discovery rules remain unchanged and all other
project tests continue to run.

Two Log4j tests involving rollover temporary-file counts and HTTP configuration
polling have demonstrated timing-sensitive failures under full-suite load. They
are not excluded. Full verification must first fail only in those allowlisted
tests, after which both are rerun in isolation; the pipeline continues only when
that focused retry passes. The full attempt and retry logs are retained.

The repository is expected at:

```text
/scratch/hpc-prf-dssecs/$USER/dsarp-openrewrite
```

Runtime and generated data are kept outside Git:

```text
/scratch/hpc-prf-dssecs/$USER/environments
/scratch/hpc-prf-dssecs/$USER/tools
/scratch/hpc-prf-dssecs/$USER/maven-repository
/scratch/hpc-prf-dssecs/$USER/runs
```

## Required external artifacts

Copy the tested Arcan 1.2.1 and RefactoringMiner 3.1.4 distributions to:

```text
tools/arcan-1.2.1/distribution/arcan-1.2.1
tools/refactoring-miner/RefactoringMiner-3.1.4
```

Fast runs also require the ignored prediction cache at:

```text
dsarp-openrewrite/shared/pipeline-cache/logging-log4j2/
4f474b32751f4ccad67424ca585612584440cd63/predictions.csv
```

## Setup

```bash
cd /scratch/hpc-prf-dssecs/$USER/dsarp-openrewrite
sbatch hpc/noctua_setup.sbatch
squeue -u $USER
```

## One laptop-sized validation batch

```bash
sbatch hpc/noctua_pipeline.sbatch
```

The default `PIPELINE_MODE=reuse_predictions` uses the shared 716-row
prediction CSV and skips mining, training, and prediction. To rebuild the
experiment from fresh RefactoringMiner output, submit with:

```bash
sbatch --export=ALL,PIPELINE_MODE=fresh,MAX_COMMITS_PER_REPO=2000,SEVERITY_CATEGORIES=high,BATCH_SIZE=1,START_BATCH=1,MAX_BATCHES=1 \
  hpc/noctua_pipeline.sbatch
```

Fresh mode does not require the shared prediction CSV. It passes `--remine`,
then prepares training data, trains the model, generates new predictions, and
continues through OpenRewrite and Arcan.

To generate predictions for a new target without retraining, select
**Predictions using trained model** in the dashboard and provide the complete
`final_model` directory from a compatible training run. The equivalent Slurm
mode is `pretrained_model`:

```bash
sbatch --export=ALL,PIPELINE_MODE=pretrained_model,PRETRAINED_MODEL_DIR=/scratch/hpc-prf-dssecs/$USER/runs/tika/34173769/models/distilbert_improved_strict_ranked_top5/final_model,STOP_STAGE=prediction \
  hpc/noctua_pipeline.sbatch
```

This mode still creates target model inputs from the three baseline CSV files,
but skips RefactoringMiner and DistilBERT training. Retrain only when the mined
training evidence, label/input schema, base model, or training configuration
changes.

`MAX_COMMITS_PER_REPO` controls the mining limit without editing the pipeline
script. Five repositories are configured, so `2000` represents a maximum of
10,000 selected commits. The generated cache manifest records both values and
the number of resulting training records.

If a fresh run stops after mining, first correct the reported dependency or
environment issue with `noctua_setup.sbatch`, then reuse the run directory with
`RESUME_RUN_ID` and `START_STAGE=training`. This preserves the completed mining
output instead of processing the 2,500 commits again.

Customize a submission without editing the script:

```bash
sbatch --export=ALL,SEVERITY_CATEGORIES=high,BATCH_SIZE=10,START_BATCH=2,MAX_BATCHES=1 \
  hpc/noctua_pipeline.sbatch
```

`MAX_BATCHES=0` processes every remaining batch. Every Slurm job writes to its
own run directory named by `SLURM_JOB_ID`.

Resume an existing run at a named pipeline stage without repeating completed
work:

```bash
sbatch --export=ALL,RESUME_RUN_ID=JOB_ID,START_STAGE=final_verify \
  hpc/noctua_pipeline.sbatch
```

`START_STAGE` is accepted only with `RESUME_RUN_ID`, preventing accidental
attempts to resume inside a new empty run directory. A `resume-*.txt` audit
record is written into the reused run directory.

Monitor and inspect:

```bash
squeue -u $USER
tail -f slurm-pipeline-JOB_ID.out
sacct -j JOB_ID --format=JobID,State,Elapsed,MaxRSS,AllocCPUS,ExitCode
```
