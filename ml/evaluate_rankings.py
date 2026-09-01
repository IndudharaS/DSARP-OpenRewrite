#!/usr/bin/env python3
"""Compute transparent held-out ranking metrics from the model comparison CSV."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


SCORE = re.compile(r"\s*\([0-9]*\.?[0-9]+\)\s*$")


def labels(value: str) -> list[str]:
    return [SCORE.sub("", item).strip() for item in str(value or "").split("|") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.comparison_csv.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("Held-out comparison CSV is empty")

    top1_hits = exact_top5 = matched = predicted_total = actual_total = 0
    per_label: dict[str, dict[str, int]] = defaultdict(lambda: {"support": 0, "top5_hits": 0})
    for row in rows:
        actual = set(labels(row.get("actual_labels", "")))
        predicted = labels(row.get("predicted_top5", ""))
        predicted_set = set(predicted)
        overlap = actual & predicted_set
        top1_hits += int(bool(predicted) and predicted[0] in actual)
        exact_top5 += int(actual == predicted_set)
        matched += len(overlap)
        predicted_total += len(predicted_set)
        actual_total += len(actual)
        for label in actual:
            per_label[label]["support"] += 1
            per_label[label]["top5_hits"] += int(label in predicted_set)

    count = len(rows)
    label_recalls = {
        label: values["top5_hits"] / values["support"]
        for label, values in per_label.items() if values["support"]
    }
    metrics = {
        "held_out_records": count,
        "top1_hit_rate": top1_hits / count,
        "exact_top5_set_accuracy": exact_top5 / count,
        "micro_precision_at_5": matched / predicted_total if predicted_total else 0.0,
        "micro_recall_at_5": matched / actual_total if actual_total else 0.0,
        "macro_label_recall_at_5": sum(label_recalls.values()) / len(label_recalls) if label_recalls else 0.0,
        "per_label": {
            label: {**per_label[label], "recall_at_5": recall}
            for label, recall in sorted(label_recalls.items())
        },
    }
    # This is deliberately strict: automatic industrial use needs more than a
    # majority-label hit. Build validation remains mandatory in every case.
    qualified = (
        count >= 50
        and metrics["top1_hit_rate"] >= 0.60
        and metrics["macro_label_recall_at_5"] >= 0.60
    )
    report = {
        "schema_version": 1,
        "metrics": metrics,
        "automatic_recommendation_qualified": qualified,
        "usage_classification": "decision_support" if qualified else "research_only",
        "qualification_requirements": {
            "minimum_held_out_records": 50,
            "minimum_top1_hit_rate": 0.60,
            "minimum_macro_label_recall_at_5": 0.60,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
