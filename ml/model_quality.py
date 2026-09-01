#!/usr/bin/env python3
"""Shared, dependency-light policies for multilabel model quality."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Sequence


def parse_labels(value: object) -> list[str]:
    """Parse pipe-separated labels, optionally followed by ``(score)``."""
    result = []
    for item in str(value or "").split("|"):
        item = item.strip()
        if not item:
            continue
        if item.endswith(")") and " (" in item:
            item = item.rsplit(" (", 1)[0].strip()
        if item:
            result.append(item)
    return result


def platt_probability(score: float, calibration: dict | None) -> float:
    """Apply a fitted one-dimensional logistic calibrator to a sigmoid score."""
    if not calibration or calibration.get("method") != "platt":
        return float(score)
    value = float(calibration.get("coefficient", 1.0)) * float(score)
    value += float(calibration.get("intercept", 0.0))
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, value))))


def select_predictions(
    scores: Sequence[float],
    labels: Sequence[str],
    thresholds: dict[str, float] | None = None,
    calibrations: dict[str, dict] | None = None,
    max_labels: int = 5,
) -> list[tuple[str, float, float]]:
    """Return only labels meeting their validation-derived threshold.

    Each tuple is ``(label, calibrated_score, raw_score)``. Returning an empty
    list is intentional: the model abstains instead of inventing candidates.
    """
    thresholds = thresholds or {}
    calibrations = calibrations or {}
    ranked = []
    for label, raw in zip(labels, scores):
        calibrated = platt_probability(float(raw), calibrations.get(label))
        threshold = float(thresholds.get(label, 0.5))
        if calibrated >= threshold:
            ranked.append((label, calibrated, float(raw)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:max(0, max_labels)]


def multilabel_metrics(actual_rows: Iterable[set[str]], predicted_rows: Iterable[list[str]]) -> dict:
    """Compute set, ranking, coverage, and per-label metrics."""
    actual_rows = list(actual_rows)
    predicted_rows = list(predicted_rows)
    if len(actual_rows) != len(predicted_rows):
        raise ValueError("Actual and predicted row counts differ")
    totals = defaultdict(int)
    per_label: dict[str, dict[str, int]] = defaultdict(lambda: {"support": 0, "predicted": 0, "hits": 0})
    example_precision = example_recall = example_f1 = jaccard = reciprocal_rank = 0.0
    exact = top1 = abstained = 0
    at_k = {k: {"matched": 0, "predicted": 0, "actual": 0} for k in (1, 3, 5)}
    for actual, predicted in zip(actual_rows, predicted_rows):
        predicted = list(dict.fromkeys(predicted))
        predicted_set = set(predicted)
        overlap = actual & predicted_set
        exact += int(actual == predicted_set)
        top1 += int(bool(predicted) and predicted[0] in actual)
        abstained += int(not predicted)
        precision = len(overlap) / len(predicted_set) if predicted_set else 0.0
        recall = len(overlap) / len(actual) if actual else 0.0
        example_precision += precision
        example_recall += recall
        example_f1 += 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        union = actual | predicted_set
        jaccard += len(overlap) / len(union) if union else 1.0
        reciprocal_rank += next((1.0 / rank for rank, label in enumerate(predicted, 1) if label in actual), 0.0)
        for label in actual:
            per_label[label]["support"] += 1
        for label in predicted_set:
            per_label[label]["predicted"] += 1
            per_label[label]["hits"] += int(label in actual)
        totals["matched"] += len(overlap)
        totals["predicted"] += len(predicted_set)
        totals["actual"] += len(actual)
        for k, values in at_k.items():
            selected = set(predicted[:k])
            values["matched"] += len(actual & selected)
            values["predicted"] += len(selected)
            values["actual"] += len(actual)
    count = len(actual_rows)
    details = {}
    for label, values in sorted(per_label.items()):
        precision = values["hits"] / values["predicted"] if values["predicted"] else 0.0
        recall = values["hits"] / values["support"] if values["support"] else 0.0
        details[label] = {**values, "precision": precision, "recall": recall,
                          "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    macro = lambda key: sum(item[key] for item in details.values()) / len(details) if details else 0.0
    micro_precision = totals["matched"] / totals["predicted"] if totals["predicted"] else 0.0
    micro_recall = totals["matched"] / totals["actual"] if totals["actual"] else 0.0
    result = {
        "held_out_records": count,
        "coverage": (count - abstained) / count if count else 0.0,
        "abstention_rate": abstained / count if count else 0.0,
        "top1_hit_rate": top1 / count if count else 0.0,
        "exact_set_accuracy": exact / count if count else 0.0,
        "mean_reciprocal_rank": reciprocal_rank / count if count else 0.0,
        "example_precision": example_precision / count if count else 0.0,
        "example_recall": example_recall / count if count else 0.0,
        "example_f1": example_f1 / count if count else 0.0,
        "mean_jaccard": jaccard / count if count else 0.0,
        "micro_precision": micro_precision,
        "micro_recall": micro_recall,
        "micro_f1": 2 * micro_precision * micro_recall / (micro_precision + micro_recall)
                    if micro_precision + micro_recall else 0.0,
        "macro_label_precision": macro("precision"),
        "macro_label_recall": macro("recall"),
        "macro_label_f1": macro("f1"),
        "per_label": details,
    }
    for k, values in at_k.items():
        result[f"micro_precision_at_{k}"] = values["matched"] / values["predicted"] if values["predicted"] else 0.0
        result[f"micro_recall_at_{k}"] = values["matched"] / values["actual"] if values["actual"] else 0.0
    # Compatibility names: selected predictions are capped at five, so these
    # describe the thresholded top-five-or-fewer policy rather than forced five.
    result["exact_top5_set_accuracy"] = result["exact_set_accuracy"]
    result["macro_label_recall_at_5"] = result["macro_label_recall"]
    return result
