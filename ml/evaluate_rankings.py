#!/usr/bin/env python3
"""Compute transparent held-out ranking metrics from the model comparison CSV."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from model_quality import multilabel_metrics, parse_labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path)
    args = parser.parse_args()
    with args.comparison_csv.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("Held-out comparison CSV is empty")

    actual = [set(parse_labels(row.get("actual_labels", ""))) for row in rows]
    predicted = [parse_labels(row.get("predicted_labels", row.get("predicted_top5", ""))) for row in rows]
    metrics = multilabel_metrics(actual, predicted)
    split = {}
    if args.split_manifest and args.split_manifest.is_file():
        split = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    cross_project = bool(split.get("cross_project_test"))
    # This is deliberately strict: automatic industrial use needs more than a
    # majority-label hit. Build validation remains mandatory in every case.
    qualified = (
        metrics["held_out_records"] >= 50
        and metrics["top1_hit_rate"] >= 0.60
        and metrics["macro_label_recall"] >= 0.60
        and metrics["macro_label_f1"] >= 0.50
        and metrics["micro_precision"] >= 0.50
        and metrics["coverage"] >= 0.50
        and cross_project
    )
    report = {
        "schema_version": 2,
        "metrics": metrics,
        "split_evidence": split,
        "automatic_recommendation_qualified": qualified,
        "usage_classification": "decision_support" if qualified else "research_only",
        "qualification_requirements": {
            "minimum_held_out_records": 50,
            "minimum_top1_hit_rate": 0.60,
            "minimum_macro_label_recall": 0.60,
            "minimum_macro_label_f1": 0.50,
            "minimum_micro_precision": 0.50,
            "minimum_coverage": 0.50,
            "cross_project_test_required": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
