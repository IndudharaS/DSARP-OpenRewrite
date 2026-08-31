# Log4j2 sequential pipeline runner

`run_log4j2_pipeline.sh` automates the reproducible Log4j2 refactoring
experiment at commit `4f474b32751f4ccad67424ca585612584440cd63`.

OpenRewrite is separated into reviewable generation and execution files:

```text
ml/validation_agent.py
openrewrite/generate_recipes.py
openrewrite/README.md
openrewrite/templates/FileSizeCompatibilityFacade.java
scripts/generate_openrewrite_recipes.sh
scripts/run_openrewrite_log4j2.sh
```

Before generation, `ml/validation_agent.py` evaluates every ranked
refactoring-type suggestion the model produced for each smell — ignoring its
confidence score — against a documented smell-to-refactoring compatibility
matrix, and selects the structurally best-fit candidate (or records that none
of the ranked suggestions are known to resolve the reported smell). Its output
CSV, not the raw model predictions, is what `generate_recipes.py` consumes.
See [../docs/VALIDATION.md](../docs/VALIDATION.md) for the full gate list.

It validates prerequisites, imports the supplied baseline Arcan CSVs, optionally
reruns mining and model inference, clones the assigned Log4j2 version, applies an
OpenRewrite recipe, runs tests, and then runs Arcan on the refactored build.

## Quick start

From the project root:

```bash
chmod +x scripts/run_log4j2_pipeline.sh
scripts/run_log4j2_pipeline.sh
```

The default run uses the retained prediction file:

```text
pipeline_results/logging_log4j2_refactoring_suggestions_from_trained_model.csv
```

This is the recommended mode when the goal is to reproduce the OpenRewrite
application and smell evaluation without retraining DistilBERT.

The reusable baseline CSV folder is:

```text
baseline_csv/
```

For another system, replace the three files in that same folder:

```bash
baseline_csv/component-metrics.csv
baseline_csv/smell-characteristics.csv
baseline_csv/smell-affects.csv
```

## Full run

To rerun commit mining, model training, Log4j2 inference, and the experiment:

```bash
scripts/run_log4j2_pipeline.sh --full
```

The full run can take hours and downloads or processes large repositories and ML
dependencies. The original notebooks are not modified. Parameterized copies are
created under `runs/log4j2/notebooks` with portable paths.

The mining script currently mines five training repositories: Tika, Maven,
Camel, Ant, and Lucene. With `--max-commits-per-repo 500`, it selects at most
2,500 candidate commits. Log4j2 is the evaluation repository and is cloned later
at its assigned commit.

RefactoringMiner 3.1.4 is launched with the bundled JDK 21 because its classes
target Java 21. Log4j2 Maven/OpenRewrite operations continue to use JDK 17. The
runner stops before training if mining produces an empty dataset.

## Clean rerun

If `runs/log4j2` already exists, the script refuses to overwrite repositories.
Use:

```bash
scripts/run_log4j2_pipeline.sh --clean
```

The old run is moved to a timestamped backup instead of being permanently
deleted. If the project-level prediction CSV is absent, a non-full clean run
automatically reuses the newest prediction CSV retained in those backups.

## Stages and resuming

The stages are:

```text
preflight
inputs
mining
training
prediction
candidate_validation
clone
baseline
rewrite
focused_test
format
final_verify
smells
summary
```

The `inputs` stage executes a parameterized copy of `csv_parser.ipynb` and
recreates the model-input CSV from the three files currently in `baseline_csv/`.
It also normalizes the notebook kernel to the project `python3` environment.

Run only through the baseline:

```bash
scripts/run_log4j2_pipeline.sh --through baseline
```

Resume after fixing an interrupted OpenRewrite stage:

```bash
scripts/run_log4j2_pipeline.sh --from focused_test
```

Run one stage by using the same value for both options:

```bash
scripts/run_log4j2_pipeline.sh --from smells --through smells
```

Do not resume at a stage unless the files produced by its preceding stages are
already present.

## Run OpenRewrite separately

After the baseline repository and experiment worktree exist, run only the
OpenRewrite operation with:

```bash
scripts/run_openrewrite_log4j2.sh \
  --repository runs/log4j2/repositories/logging-log4j2-openrewrite \
  --results-dir runs/log4j2/results \
  --log-dir runs/log4j2/logs
```

Dry run only:

```bash
scripts/run_openrewrite_log4j2.sh \
  --repository runs/log4j2/repositories/logging-log4j2-openrewrite \
  --mode dry-run
```

The operation script receives an explicit generated YAML file and active recipe
name; it no longer generates a hidden recipe inside the main runner.

The main pipeline processes every model prediction with the generic generator.
Its manifest, individual candidates, safety exclusions, and aggregate applicable
recipe are written to:

```text
runs/log4j2/results/generated-openrewrite/
```

Inspect the complete manifest with:

```bash
jq '.status_counts' runs/log4j2/results/generated-openrewrite/manifest.json
```

Non-public candidates are applied in isolated disposable worktrees, formatted
with Spotless, and checked with `mvn verify -DskipTests`. Public API candidates
are routed to manual review unless a compatibility strategy is registered. This
includes Log4j2's BND public-API baseline checks. The validation report and the
configuration ultimately applied are:

```text
runs/log4j2/results/openrewrite-validation/validation-report.csv
runs/log4j2/results/openrewrite-validation/validation-report.json
runs/log4j2/results/openrewrite-validation/validated-candidates.yml
```

The Log4j2 defaults can also be supplied explicitly:

```bash
scripts/run_log4j2_pipeline.sh --full --clean \
  --project-name logging-log4j2 \
  --repository-url https://github.com/apache/logging-log4j2.git \
  --version-id 4f474b32751f4ccad67424ca585612584440cd63 \
  --baseline-csv-dir baseline_csv \
  --model-inputs pipeline_results/logging_log4j2_model_inputs_from_csv.csv
```

## Output structure

The default run directory is:

```text
runs/log4j2/
├── repositories/
│   ├── logging-log4j2/
│   └── logging-log4j2-openrewrite/
├── results/
│   ├── arcan-supplied-baseline-summary.json
│   ├── arcan-baseline-matched/
│   │   ├── input-manifest.json
│   │   ├── raw/
│   │   └── summary.json
│   ├── arcan-refactored/
│   │   ├── input-manifest.json
│   │   ├── raw/
│   │   └── summary.json
│   ├── arcan-comparison.json
│   ├── arcan-comparison.csv
│   ├── experiment-report.json
│   ├── model-evaluation.json
│   ├── training-data-quality.json
│   ├── validation-agent-report.json
│   ├── openrewrite-validation/
│   │   ├── validation-report.csv
│   │   ├── validation-report.json
│   │   └── validated-candidates.yml
│   ├── openrewrite-dry-run.patch
│   └── arcan-comparison.txt
├── logs/
│   ├── baseline-verify.log
│   ├── rewrite-dry-run.log
│   ├── rewrite-run.log
│   ├── focused-tests.log
│   └── refactored-verify.log
└── notebooks/                 # Used by --full
```

Each `input-manifest.json` contains the sorted relative path, size, and SHA-256
digest of every compiled class analyzed by Arcan. The comparison is marked as
causal evidence only when the pinned Arcan version and configuration match and
the before/after class-path populations differ by no more than 1% (with a
minimum allowance of five paths for legitimate small refactorings). Cycle rows
are canonicalized and deduplicated before aggregate counts are compared.

No Git commit is created automatically.

## Arcan smell detection

The three CSV files currently placed directly in `baseline_csv/` are imported
as the pre-refactoring baseline.
Arcan is not rerun on the untouched repository. The pipeline uses the pinned
open-source Arcan 1.2.1 distribution under `tools/arcan-1.2.1` only after
OpenRewrite and the refactored Maven build. It detects cyclic dependencies,
hub-like dependencies, and unstable dependencies and exports package/class
metrics.

Run Arcan separately on any already-built Maven repository:

```bash
scripts/run_arcan_smells.sh \
  --repository /absolute/path/to/repository \
  --output-dir /absolute/path/to/arcan-output
```

The wrapper discovers every `target/classes` directory automatically and does
not contain Log4j-specific package paths. `module-info.class` is omitted because
Arcan 1.2.1's bytecode reader predates the Java module constant-pool format.
The complete raw CSVs remain available for auditing; `summary.json` only counts
and organizes those reports.

Compare two completed Arcan runs manually:

```bash
.venv/bin/python evaluation/summarize_arcan.py compare \
  before/summary.json after/summary.json \
  --output arcan-comparison.json
```

The baseline importer records the `project` and `versionId` metadata from the
supplied files. Arcan 1.2.1 is a reproducible post-refactoring detector, but
differences must be interpreted carefully when the baseline files were produced
by another Arcan release or configuration.

## Expected build condition

At the assigned historical commit, both the untouched baseline and refactored
tree have an environment-sensitive Spring Boot test failure:

```text
Log4j2SpringBootInitTest.testEnvironment
expected: 1
actual: 5
```

The runner accepts only this exact known failure. Any other Maven failure stops
the pipeline.

## Interpreting the smell result

The experiment targets the direct dependency cycle between:

```text
org.apache.logging.log4j.core.appender.rolling
org.apache.logging.log4j.core.appender.rolling.action
```

Use `arcan-comparison.json` to check whether the cycle set containing these
packages is listed under `resolved`, `introduced`, or `unchanged`. Do not rely
only on total-count differences: unrelated smells can be added or removed in
the same refactoring run.

## Custom run directory

```bash
scripts/run_log4j2_pipeline.sh \
  --run-root /absolute/path/to/log4j2-run
```

Use an absolute path so Maven and OpenRewrite configuration paths remain clear.

## Safety behavior

- Existing runs are never overwritten silently.
- `--clean` moves the previous run to a timestamped backup.
- OpenRewrite first runs in dry-run mode and saves its patch.
- The generated aggregate, execution logic, and compatibility template are separate files.
- The script stops on unexpected command or test failures.
- Baseline CSV files are read-only pipeline inputs.
- The resulting Git changes remain uncommitted for review.

## Review commands

After completion:

```bash
git -C runs/log4j2/repositories/logging-log4j2-openrewrite status --short
git -C runs/log4j2/repositories/logging-log4j2-openrewrite diff --check
git -C runs/log4j2/repositories/logging-log4j2-openrewrite diff --stat
git -C runs/log4j2/repositories/logging-log4j2-openrewrite diff
```

Inspect the comparison:

```bash
cat runs/log4j2/results/arcan-comparison.txt
```

## Important scientific limitation

The baseline uses the supplied three-file Arcan export while the post-refactoring
analysis uses pinned Arcan 1.2.1. Counts from different Arcan releases or
configurations are not automatically equivalent. For publication-level claims,
use the same Arcan version, thresholds, build scope, and configuration that
created the supplied baseline files.
