# Generic repository pipeline

`run_generic_pipeline.sh` runs the complete workflow for a Maven-based Java
system at an exact Git revision. It requires a Maven wrapper (`mvnw`) in the
target repository.

## Required input

Place exactly these current-system Arcan exports in one folder:

```text
baseline_csv/
├── component-metrics.csv
├── smell-characteristics.csv
└── smell-affects.csv
```

The `project` column must equal `--system`, and every `versionId` must equal the
value supplied through `--version-id`.

## Complete run

```bash
chmod +x scripts/run_generic_pipeline.sh scripts/run_openrewrite_maven.sh

scripts/run_generic_pipeline.sh \
  --system SYSTEM_NAME \
  --repository-url https://github.com/ORGANIZATION/REPOSITORY.git \
  --version-id EXACT_COMMIT_HASH \
  --baseline-csv-dir baseline_csv \
  --clean
```

This mode reuses the shared RefactoringMiner output when available, trains the model,
exports its top-five ranked recommendations for the supplied CSV data,
generates OpenRewrite
recipes, validates candidates in disposable worktrees, applies the validated
aggregate, verifies the complete Maven build, runs Arcan, and compares smells.

## Shared RefactoringMiner cache

CLI and web experiments use the same default cache:

```text
shared/refactoring-miner/default/
├── work/       # the five training repositories
└── output/     # raw commits, raw smell instances and final training records
```

When `output/arcan_style_training_dataset.jsonl` exists, a full experiment
reuses it and skips commit mining. Every experiment still trains its own model
and creates predictions for its own uploaded baseline CSV files.

To intentionally rebuild the cache:

```bash
scripts/run_generic_pipeline.sh \
  --system SYSTEM_NAME \
  --repository-url REPOSITORY_URL \
  --version-id EXACT_COMMIT_HASH \
  --baseline-csv-dir baseline_csv \
  --remine
```

The previous shared cache is moved to a timestamped backup. Use
`--mining-cache-dir PATH` when an experiment needs a completely separate cache.
Concurrent CLI and web experiments are locked so only one process updates a
given cache; other experiments wait and then reuse the completed output.

## Reuse an existing prediction CSV

```bash
scripts/run_generic_pipeline.sh \
  --system SYSTEM_NAME \
  --repository-url https://github.com/ORGANIZATION/REPOSITORY.git \
  --version-id EXACT_COMMIT_HASH \
  --baseline-csv-dir baseline_csv \
  --predictions-csv /absolute/path/to/predictions.csv \
  --clean
```

The prediction CSV may omit `project` and `versionId`. When present, they must
match the supplied inputs. It must contain the columns `architecture_smell`,
`affected_elements`, and `suggestions`.

When a compatible prediction cache already exists, use:

```bash
scripts/run_generic_pipeline.sh \
  --system logging-log4j2 \
  --repository-url https://github.com/apache/logging-log4j2.git \
  --version-id 4f474b32751f4ccad67424ca585612584440cd63 \
  --baseline-csv-dir baseline_csv \
  --reuse-predictions
```

This resolves only
`shared/pipeline-cache/<system>/<commit>/predictions.csv`; predictions are
never silently reused for a different commit.

## Retrain without mining again

Use a retained `arcan_style_training_dataset.jsonl` to skip RefactoringMiner
while still running training, prediction and evaluation:

```bash
scripts/run_generic_pipeline.sh \
  --system SYSTEM_NAME \
  --repository-url REPOSITORY_URL \
  --version-id EXACT_COMMIT_HASH \
  --baseline-csv-dir baseline_csv \
  --training-dataset /absolute/path/to/arcan_style_training_dataset.jsonl \
  --clean
```

## Experimental public-API candidates

For exploratory runs, `--allow-risky-candidates` permits candidates normally
held for manual review to be executed in isolated worktrees. This does not mark
them valid automatically: a concrete source change and successful Maven
verification are still required, and downstream API compatibility remains a
separate concern.

## Outputs

For `--system example`, results are written to:

```text
runs/example/results/
├── generated-openrewrite/
├── openrewrite-validation/
├── arcan-baseline-matched/
├── arcan-refactored/
├── arcan-comparison.csv
├── arcan-comparison.json
├── experiment-report.json
├── model-evaluation.json
├── training-data-quality.json
└── arcan-comparison.txt
```

Only candidates that pass isolated `mvn verify -DskipTests` are applied. Public
API moves require a registered compatibility strategy or remain `manual_review`.
The
final refactored repository must then pass its normal `mvn verify` before Arcan
is allowed to run. Failed or underspecified recommendations remain documented
in the manifests; they are never silently forced into the codebase.

The prediction CSV stores the model's unconditional top-five labels and scores
in `suggestions`, matching the original notebook behavior. These are ranked
candidates, not confidence-filtered guarantees; OpenRewrite and Maven validation
still decide whether a concrete recommendation is safe to apply.

Training separates records by commit (so one commit cannot leak across train and
evaluation partitions), uses bounded inverse-frequency label weights, and chooses
the best checkpoint by macro F1. Recipe generation searches all five ranked
labels for an executable recommendation and emits every distinct repository-
backed candidate it can safely concretize, with no numerical candidate cap. It
records model rank, score, structural score, risk, and the exact validation
failure category in its CSV/JSON reports.
The supplied historical CSV summary is retained for traceability, but causal
before/after deltas use fresh baseline and refactored runs from the same pinned
Arcan version and configuration. `experiment-report.json` explicitly states
whether a causal claim is allowed.

## Resume

```bash
scripts/run_generic_pipeline.sh \
  --system SYSTEM_NAME \
  --repository-url REPOSITORY_URL \
  --version-id EXACT_COMMIT_HASH \
  --baseline-csv-dir baseline_csv \
  --predictions-csv /absolute/path/to/predictions.csv \
  --from rewrite
```

Do not use `--clean` when resuming an existing run.
