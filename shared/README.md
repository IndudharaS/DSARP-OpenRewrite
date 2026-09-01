# Shared reusable artifacts

`trained-model/default/final_model` is the automatically selected model used by
prediction-only runs. Retraining does not automatically make a model the
default: it must pass the held-out, cross-project precision, recall, F1,
coverage, and sample-size gates in `model-evaluation.json`.

Models that finish training but fail a gate are preserved under
`trained-model/candidates/<Slurm-job-id>/`. They remain research artifacts and
do not replace the last qualified default. Score calibration and per-label
decision thresholds are stored in `final_model/labels.json`; older models
without these fields remain readable and use a conservative 0.5 fallback.

This directory contains caches reused by CLI and web experiments. Generated or
large contents are intentionally excluded from Git; the directory documentation
and small provenance records remain versionable.

## Layout

- `refactoring-miner/default/output/`: mined training data shared by full runs.
- `refactoring-miner/default/work/`: temporary clones and miner workspace.
- `pipeline-cache/<system>/<commit>/predictions.csv`: predictions compatible
  with exactly one system and commit. Successful web runs refresh this cache.
- `pipeline-cache/<system>/<commit>/provenance.json`: repository and source-run
  identity for the cached predictions.
- `trained-model/default/final_model/`: active model automatically reused by
  prediction-only runs. Only qualified retraining atomically replaces this
  default and retains the previous version in a timestamped backup.
- `trained-model/candidates/<job-id>/`: completed research models that did not
  satisfy every automatic-promotion gate. Model weights are excluded from Git.

Never reuse predictions for a different commit: their source entities and
packages may no longer exist. Use a full workflow to regenerate them.
