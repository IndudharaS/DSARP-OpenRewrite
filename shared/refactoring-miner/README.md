# Shared RefactoringMiner cache

Both the CLI and web dashboard use `default/` unless another cache directory is
provided explicitly.

```text
default/
├── cache-manifest.json
├── work/repositories/       # Tika, Maven, Camel, Ant and Lucene clones
└── output/
    ├── raw_commits.csv
    ├── raw_commits.jsonl
    ├── raw_smell_instances.csv
    ├── raw_smell_instances.jsonl
    ├── arcan_style_training_dataset.csv
    ├── arcan_style_training_dataset.jsonl
    ├── mining_errors.csv
    └── repository_summary.csv
```

A normal full experiment reuses the cached JSONL dataset and proceeds directly
to model training. Use CLI `--remine` or the web interface's **Generate fresh
RefactoringMiner output** checkbox to rebuild it. The old cache is retained in a
timestamped backup.

Use CLI `--mining-cache-dir PATH` when an experiment needs an independent cache.
An atomic lock prevents multiple experiments from rebuilding the same cache at
the same time.
