# Architecture Refactoring Experiment Platform

This repository is a reproducible research pipeline for generating, applying,
and evaluating architecture-refactoring recommendations on Java/Maven systems.
It combines mined historical refactorings, a multi-label recommendation model,
repository-backed OpenRewrite recipe generation, isolated build validation, and
matched before/after Arcan measurement.

## Evidence levels

The platform deliberately separates four different claims:

1. **Model suggestion** — a ranked, probabilistic refactoring label.
2. **Concrete candidate** — repository analysis supplied the parameters needed
   by an OpenRewrite recipe.
3. **Validated refactoring** — the isolated source change passed formatting,
   compilation, tests/build verification, and configured API checks.
4. **Measured architectural result** — before and after were analyzed with the
   same pinned Arcan version and configuration.

Only levels 3 and 4 support an applied-refactoring result. A high model score is
never treated as proof that a source change is safe or beneficial.

## Current scientific status

The bundled historical corpus is small. Its original 148 rows contain 30 rows
whose selected refactoring has zero textual overlap with the affected smell.
The quality gate removes those rows and labels with fewer than five examples.
The previous model evaluation used only 22 held-out records and therefore is
classified as `research_only`, not qualified for autonomous industrial use.

The system is suitable for thesis experiments and guarded decision support. It
must not be represented as a generally validated industrial recommender until a
larger independently labelled corpus and external-project evaluation satisfy the
documented qualification thresholds.

## Run the web application

```bash
cd /Users/indudhara/Documents/Masters_Project/29-jul
scripts/run_web_dashboard.sh
```

Open <http://127.0.0.1:8765>. For Logging-Log4j2, select the preset, upload the
three baseline CSV files, keep fresh mining unchecked, and start a full run.
The shared mining cache is reused automatically.

On Noctua, start the Slurm-enabled interface with
`scripts/run_web_dashboard.sh --hpc` and forward port 8765 over SSH. The same
interface then submits compute jobs, reports queue/node status, streams logs,
cancels jobs, resumes existing run IDs, and exposes completed artifacts. See
[Noctua execution](hpc/README.md) for exact commands.

## Run from the CLI

```bash
scripts/run_generic_pipeline.sh \
  --system logging-log4j2 \
  --repository-url https://github.com/apache/logging-log4j2.git \
  --version-id 4f474b32751f4ccad67424ca585612584440cd63 \
  --baseline-csv-dir baseline_csv \
  --clean
```

The `logging-log4j2` system name automatically enables its registered
compatibility profile. Add `--remine` only when intentionally rebuilding the
shared training corpus.

## Principal outputs

Every run writes:

- `training-data-quality.json` — accepted/rejected training evidence;
- `model-evaluation.json` — held-out ranking metrics and qualification status;
- `generated-openrewrite/manifest.{json,csv}` — all predictions and candidate evidence;
- `openrewrite-validation/validation-report.{json,csv}` — isolated build results;
- `arcan-baseline-matched/` and `arcan-refactored/` — same-tool raw measurements;
- `arcan-comparison.{json,csv}` — comparable before/after deltas;
- `experiment-report.json` — revision, diff, hashes, validation, and causal-claim verdict.

Run the fast regression suite with:

```bash
chmod +x scripts/check_project.sh
scripts/check_project.sh
```

See [generic pipeline documentation](scripts/README-generic-pipeline.md),
[OpenRewrite methodology](openrewrite/README.md), and
[web dashboard documentation](webui/README.md) for details.

## Repository layout

- `scripts/`: CLI entry points for mining, prediction, OpenRewrite and Arcan.
- `ml/`: training-data preparation, inference and ranking evaluation.
- `openrewrite/`: generic recipe generation and checked-in templates.
- `evaluation/`: validation, provenance and before/after smell reporting.
- `webui/`: local/Slurm dashboard; generated run workspaces are ignored by Git.
- `shared/`: reusable local caches keyed by profile or system and commit.
- `baseline_csv/`: the three baseline Arcan CSV inputs for the selected system.
- `tests/`: fast project-level regression tests.
- `tools/`: locally installed third-party tools; binaries are ignored by Git.

Large models, mined repositories, prediction CSVs, tool distributions and run
workspaces are deliberately not committed. Their expected locations and
regeneration rules are documented under `shared/` and `scripts/`.
