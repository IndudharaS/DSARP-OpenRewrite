# Prediction cache

Prediction CSVs are stored under `<system>/<commit>/predictions.csv`. The web
dashboard's **Fast verified run** and the CLI `--reuse-predictions` option use
only an exact system-and-commit match. CSVs and model reports are locally
generated and ignored by Git; `provenance.json` records remain available for
traceability.
