# Refactoring Lab web dashboard

The dashboard is a localhost-only interface for the generic Maven pipeline. It
can execute locally or act as a lightweight Noctua control plane that submits
the heavy pipeline to Slurm compute nodes.

## Start

```bash
cd /Users/indudhara/Documents/Masters_Project/29-jul
chmod +x scripts/run_web_dashboard.sh
scripts/run_web_dashboard.sh
```

Open <http://127.0.0.1:8765> and keep the terminal running. Stop the server with
`Ctrl+C`. A different port can be selected with `--port 9000`.

## Features

- offers outcome-based workflows for mining-only output, prediction generation
  from an existing trained model, intentional retraining from shared mining
  data, complete shared/fresh runs, fast verified runs, and uploaded inputs;
- automatically uses `shared/trained-model/default/final_model` for
  prediction-only experiments; these generate target inputs but skip mining
  and training;
- publishes every completed retraining result as the new default model while
  moving the previous default to a timestamped backup;
- lets fresh workflows set commits per training repository and shows the
  calculated maximum across all five repositories;
- reports the shared cache's configured maximum, actual mined commit rows and
  generated training-record count before submission;
- fast verified mode reuses `shared/pipeline-cache/<system>/<commit>/predictions.csv`
  for the same system and commit,
  skipping RefactoringMiner, model training and prediction while retaining
  OpenRewrite validation, build verification and matched Arcan analysis;
- reuses `shared/refactoring-miner/default` across web and CLI experiments;
- previews every included, reused, final and excluded stage before submission;
- provides presets for Tika, Karaf, Struts, Logging-Log4j2 and Cassandra and
  fills their repository URLs and assigned revisions automatically;
- uploads and validates the three baseline Arcan CSV files;
- isolates every experiment below `webui/state/runs/RUN_ID/workspace`;
- reports the active pipeline stage, total/stage timers and streams the combined
  process log;
- stops an active process group on explicit confirmation;
- optionally validates public-API/manual-review candidates after a warning and
  explicit confirmation; these still run in isolated worktrees and must pass
  Maven verification before inclusion;
- asks which High, Medium and Low/Small smell categories to process, how many
  candidates belong in each batch, and how many batches to execute; zero maximum
  batches means process all batches;
- shows prediction samples, generated/validated recipe counts and reasons;
- presents Arcan before/after metrics and comparison warnings;
- downloads prediction, manifest, validation and Arcan artifacts;
- retains run metadata and logs across dashboard restarts.
- promotes predictions from successful runs into the shared system-and-commit
  cache so deleting old run folders does not break future fast runs.

The server binds to `127.0.0.1` by default and accepts repository URLs and exact
Git commit hashes. Uploaded files are limited to 50 MB each. The target project
must be Maven-based and include an executable `mvnw`.
The built-in Logging-Log4j2 selection enables its API-compatibility validation
profile automatically; other presets and custom systems use the generic Maven
OpenRewrite executor.

## Noctua HPC dashboard

Start the persistent server on `n2login1` from the scratch clone. The server only handles
HTTP requests and Slurm commands; Maven, model and Arcan work runs in an
allocated compute job.

```bash
cd /scratch/hpc-prf-dssecs/$USER/dsarp-openrewrite
hpc/manage_dashboard.sh start
hpc/manage_dashboard.sh status
```

On the Mac, keep a second terminal open with this SSH tunnel (password-only
configuration is supported):

```bash
ssh -N -L 8765:127.0.0.1:8765 n2login1
```

Then open <http://127.0.0.1:8765>. Choose **Noctua HPC (Slurm)**, select the
workflow, severity categories and batches, upload the baseline CSVs, and press
**Start experiment**. The UI displays the Slurm job ID, pending reason, compute
node, pipeline stage, combined output/error log, results and artifacts. Closing
the browser or losing SSH does not stop the Slurm job; reconnect the tunnel and
open the dashboard again.

To continue an existing logical run, select **Resume an existing HPC run**, enter
the original run ID (for example `33917736`) and choose the first stage that
must run. **Stop run** calls `scancel` for queued/running HPC jobs. The dashboard
does not execute `sbatch` unless it was explicitly started with `--hpc`.

## Severity and batches

Severity is an explicit prioritization heuristic, not a model confidence score.
Cyclic and hub-like dependencies receive a base weight of 3, unstable
dependencies 2, and other smell types 1. Smells affecting at least four elements
receive one additional point and those affecting at least eight receive two.
Scores 4+ are High, 2-3 are Medium, and 1 is Low/Small. The manifest records the
score and explanation for every prediction.

Candidates are ordered High, Medium, Low and then divided into the configured
batch size. Candidate validation remains isolated. `Number of batches = 1` is a
short laptop run; zero processes all batches from the selected starting batch.
Set `Start from batch` to 2, 3, and so on in later experiments. Candidates
outside the selected window are reported as `deferred_batch_limit`.
