# Baseline Arcan CSV input

This is one reusable input folder. Put the currently selected software system's
three original Arcan export files directly inside it:

```text
baseline_csv/
├── component-metrics.csv
├── smell-characteristics.csv
└── smell-affects.csv
```

All three filenames are required. After finishing one system, delete or replace
these three CSV files with the next system's files. Keep the filenames unchanged
because the importer validates their schemas and reads the repository name and
version/commit from their `project` and `versionId` columns.

Select the folder when running the pipeline:

```bash
scripts/run_log4j2_pipeline.sh \
  --baseline-csv-dir baseline_csv
```

`baseline_csv` is already the pipeline default, so the option can normally be
omitted. These CSVs are treated as the authoritative pre-refactoring baseline.
Arcan is therefore run only after OpenRewrite and the refactored build.
