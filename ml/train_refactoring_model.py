#!/usr/bin/env python3
"""Train, calibrate, and evaluate the multilabel refactoring classifier."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from datasets import Dataset
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.preprocessing import MultiLabelBinarizer
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
)

from model_quality import select_predictions


def read_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["labels_list"] = [item for item in str(row["selected_refactoring_labels"]).split("|") if item]
            if not row.get("evidence_group_id"):
                normalized = re.sub(r"\s+", " ", str(row["input_text"]).strip().lower())
                row["evidence_group_id"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
            rows.append(row)
    if not rows:
        raise SystemExit("Training dataset is empty")
    return rows


def choose_repository_split(rows: list[dict]) -> tuple[set[str], set[str]]:
    repositories = sorted({str(row["repository"]) for row in rows})
    if len(repositories) < 3:
        raise SystemExit("Leakage-safe training requires at least three repositories")
    all_labels = {label for row in rows for label in row["labels_list"]}
    best = None
    for test in repositories:
        for validation in repositories:
            if validation == test:
                continue
            train_labels = {label for row in rows if row["repository"] not in {test, validation}
                            for label in row["labels_list"]}
            missing = len(all_labels - train_labels)
            test_size = sum(row["repository"] == test for row in rows) / len(rows)
            validation_size = sum(row["repository"] == validation for row in rows) / len(rows)
            score = (missing, abs(test_size - 0.20) + abs(validation_size - 0.15), test, validation)
            if best is None or score < best[0]:
                best = (score, test, validation)
    return {best[1]}, {best[2]}


def sigmoid(logits: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(logits, -40, 40)))


def calibrate(validation_scores: np.ndarray, validation_targets: np.ndarray, labels: list[str]) -> dict:
    result = {}
    for index, label in enumerate(labels):
        target = validation_targets[:, index]
        if target.sum() < 3 or (len(target) - target.sum()) < 3:
            result[label] = {"method": "identity", "reason": "insufficient_validation_support"}
            continue
        model = LogisticRegression(random_state=42).fit(validation_scores[:, [index]], target)
        result[label] = {"method": "platt", "coefficient": float(model.coef_[0][0]),
                         "intercept": float(model.intercept_[0]), "validation_support": int(target.sum())}
    return result


def apply_calibration(scores: np.ndarray, labels: list[str], calibration: dict) -> np.ndarray:
    output = scores.copy()
    for index, label in enumerate(labels):
        item = calibration.get(label, {})
        if item.get("method") == "platt":
            values = item["coefficient"] * scores[:, index] + item["intercept"]
            output[:, index] = sigmoid(values)
    return output


def tune_thresholds(scores: np.ndarray, targets: np.ndarray, labels: list[str]) -> dict[str, float]:
    thresholds = {}
    candidates = np.arange(0.10, 0.91, 0.025)
    for index, label in enumerate(labels):
        target = targets[:, index]
        if target.sum() < 2:
            thresholds[label] = 0.5
            continue
        ranked = []
        for threshold in candidates:
            predicted = scores[:, index] >= threshold
            recall = float((predicted & (target == 1)).sum() / target.sum())
            ranked.append((f1_score(target, predicted, zero_division=0), recall, -abs(threshold - 0.5), threshold))
        thresholds[label] = float(max(ranked)[3])
    return thresholds


class WeightedTrainer(Trainer):
    def __init__(self, *args, positive_weights: torch.Tensor, **kwargs):
        super().__init__(*args, **kwargs)
        self.positive_weights = positive_weights

    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        loss = torch.nn.functional.binary_cross_entropy_with_logits(
            outputs.logits, labels.float(), pos_weight=self.positive_weights.to(outputs.logits.device)
        )
        return (loss, outputs) if return_outputs else loss


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--model-output", required=True, type=Path)
    parser.add_argument("--base-model", default="distilbert-base-uncased")
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    rows = read_rows(args.dataset)
    test_repositories, validation_repositories = choose_repository_split(rows)
    split_rows = {
        "train": [row for row in rows if row["repository"] not in test_repositories | validation_repositories],
        "validation": [row for row in rows if row["repository"] in validation_repositories],
        "test": [row for row in rows if row["repository"] in test_repositories],
    }
    train_fingerprints = {row.get("evidence_group_id") for row in split_rows["train"]}
    before_validation = len(split_rows["validation"])
    split_rows["validation"] = [row for row in split_rows["validation"]
                                if row.get("evidence_group_id") not in train_fingerprints]
    validation_fingerprints = {row.get("evidence_group_id") for row in split_rows["validation"]}
    before_test = len(split_rows["test"])
    split_rows["test"] = [row for row in split_rows["test"]
                          if row.get("evidence_group_id") not in train_fingerprints | validation_fingerprints]
    leakage_exclusions = {
        "validation": before_validation - len(split_rows["validation"]),
        "test": before_test - len(split_rows["test"]),
    }
    if not split_rows["validation"] or not split_rows["test"]:
        raise SystemExit("Leakage filtering left an empty validation or test partition")
    labels = sorted({label for row in split_rows["train"] for label in row["labels_list"]})
    excluded = sorted({label for row in rows for label in row["labels_list"]} - set(labels))
    if excluded:
        raise SystemExit(f"Chosen split leaves labels absent from training: {excluded}")
    mlb = MultiLabelBinarizer(classes=labels)
    mlb.fit([labels])
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)

    def make_dataset(items: list[dict]) -> Dataset:
        frame = pd.DataFrame({
            "input_text": [str(row["input_text"]) for row in items],
            "labels": [vector.astype(np.float32).tolist()
                       for vector in mlb.transform([row["labels_list"] for row in items])],
        })
        dataset = Dataset.from_pandas(frame, preserve_index=False)
        return dataset.map(lambda batch: tokenizer(batch["input_text"], truncation=True,
                                                   max_length=args.max_length), batched=True)

    datasets = {name: make_dataset(items) for name, items in split_rows.items()}
    train_targets = mlb.transform([row["labels_list"] for row in split_rows["train"]])
    positives = train_targets.sum(axis=0)
    weights = np.clip((len(train_targets) - positives) / np.maximum(positives, 1), 1.0, 20.0)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model, num_labels=len(labels), problem_type="multi_label_classification",
        id2label={index: label for index, label in enumerate(labels)},
        label2id={label: index for index, label in enumerate(labels)},
    )
    training_dir = args.model_output / "trainer"
    training_args = TrainingArguments(
        output_dir=str(training_dir), learning_rate=2e-5,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=max(args.batch_size, 16), num_train_epochs=args.epochs,
        weight_decay=0.01, warmup_ratio=0.1, eval_strategy="epoch", save_strategy="epoch",
        load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False,
        save_total_limit=2, seed=args.seed, report_to="none",
    )
    trainer = WeightedTrainer(
        model=model, args=training_args, train_dataset=datasets["train"],
        eval_dataset=datasets["validation"], processing_class=tokenizer,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
        positive_weights=torch.tensor(weights, dtype=torch.float32),
    )
    trainer.train()
    validation_logits = trainer.predict(datasets["validation"]).predictions
    test_logits = trainer.predict(datasets["test"]).predictions
    validation_targets = mlb.transform([row["labels_list"] for row in split_rows["validation"]])
    calibration = calibrate(sigmoid(validation_logits), validation_targets, labels)
    validation_scores = apply_calibration(sigmoid(validation_logits), labels, calibration)
    thresholds = tune_thresholds(validation_scores, validation_targets, labels)

    final_model = args.model_output / "final_model"
    final_model.mkdir(parents=True, exist_ok=True)
    trainer.save_model(final_model)
    tokenizer.save_pretrained(final_model)
    metadata = {
        "schema_version": 2, "id2label": {str(index): label for index, label in enumerate(labels)},
        "label2id": {label: index for index, label in enumerate(labels)}, "max_length": args.max_length,
        "decision_thresholds": thresholds, "calibration": calibration, "maximum_labels": 5,
        "training_policy": {"group_split": "repository", "weighted_loss": True,
                            "early_stopping": True, "seed": args.seed},
    }
    (final_model / "labels.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    test_scores = sigmoid(test_logits)
    comparison = args.model_output / "test_set_prediction_comparison.csv"
    with comparison.open("w", newline="", encoding="utf-8") as handle:
        fields = ["repository", "commit", "architecture_smell", "affected_elements", "actual_labels",
                  "predicted_labels", "prediction_status", "predicted_top5"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row, scores in zip(split_rows["test"], test_scores):
            selected = select_predictions(scores, labels, thresholds, calibration, 5)
            legacy = sorted(zip(labels, scores), key=lambda item: item[1], reverse=True)[:5]
            writer.writerow({
                "repository": row["repository"], "commit": row["commit"],
                "architecture_smell": row["architecture_smell"], "affected_elements": row["affected_elements"],
                "actual_labels": " | ".join(row["labels_list"]),
                "predicted_labels": " | ".join(f"{label} ({score:.4f})" for label, score, _ in selected),
                "prediction_status": "recommended" if selected else "abstained_low_confidence",
                "predicted_top5": " | ".join(f"{label} ({score:.4f})" for label, score in legacy),
            })
    manifest = {
        "schema_version": 1, "strategy": "leave-whole-repositories-out",
        "cross_project_test": True, "test_repositories": sorted(test_repositories),
        "validation_repositories": sorted(validation_repositories),
        "train_repositories": sorted({row["repository"] for row in split_rows["train"]}),
        "record_counts": {name: len(items) for name, items in split_rows.items()},
        "group_key": ["repository", "commit", "evidence_group_id"],
        "cross_partition_fingerprint_records_excluded": leakage_exclusions,
        "calibration_fit_on": "validation_only",
        "thresholds_tuned_on": "validation_only", "label_support_train": dict(Counter(
            label for row in split_rows["train"] for label in row["labels_list"])),
    }
    (args.model_output / "split-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Trained {len(labels)} labels; comparison: {comparison}")


if __name__ == "__main__":
    main()
