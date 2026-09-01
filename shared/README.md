# Shared reusable artifacts

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
  prediction-only runs. Successful retraining atomically replaces this default
  and retains the previous version in a timestamped backup. Model weights are
  excluded from Git.

Never reuse predictions for a different commit: their source entities and
packages may no longer exist. Use a full workflow to regenerate them.
