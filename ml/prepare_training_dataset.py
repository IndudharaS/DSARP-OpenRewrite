#!/usr/bin/env python3
"""Validate and filter mined JSONL records before model training."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


REQUIRED = {
    "repository", "commit", "architecture_smell", "affected_elements",
    "input_text", "selected_refactoring_labels", "ranked_label_details_json",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--minimum-records", type=int, default=50)
    parser.add_argument("--minimum-label-support", type=int, default=5)
    parser.add_argument("--recommended-label-support", type=int, default=25)
    args = parser.parse_args()

    accepted: list[dict] = []
    reasons: Counter[str] = Counter()
    seen: set[tuple[str, ...]] = set()
    raw_count = 0
    for number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw_count += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON on line {number}: {exc}") from exc
        missing = sorted(REQUIRED - row.keys())
        if missing:
            raise SystemExit(f"Training row {number} is missing fields: {', '.join(missing)}")
        selected = {value for value in str(row["selected_refactoring_labels"]).split("|") if value}
        try:
            ranked = json.loads(row["ranked_label_details_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Training row {number} has invalid ranked_label_details_json") from exc
        evidence = {
            str(item.get("label")): float(item.get("overlap_score", 0) or 0)
            for item in ranked if isinstance(item, dict)
        }
        supported = sorted(label for label in selected if evidence.get(label, 0.0) > 0.0)
        if not supported:
            reasons["zero_overlap_evidence"] += 1
            continue
        if set(supported) != selected:
            reasons["partially_unsupported_labels_removed"] += len(selected - set(supported))
            row["selected_refactoring_labels"] = "|".join(supported)
            row["target_text"] = " | ".join(supported)
        normalized_input = re.sub(r"\s+", " ", str(row["input_text"]).strip().lower())
        row["evidence_group_id"] = hashlib.sha256(normalized_input.encode("utf-8")).hexdigest()[:16]
        key = (
            str(row["repository"]), str(row["commit"]), str(row["architecture_smell"]),
            str(row["affected_elements"]), str(row["selected_refactoring_labels"]),
        )
        if key in seen:
            reasons["duplicate"] += 1
            continue
        seen.add(key)
        accepted.append(row)

    initial_labels: Counter[str] = Counter()
    for row in accepted:
        initial_labels.update(value for value in str(row["selected_refactoring_labels"]).split("|") if value)
    rare_labels = {label for label, count in initial_labels.items() if count < args.minimum_label_support}
    if rare_labels:
        filtered: list[dict] = []
        for row in accepted:
            labels = [value for value in str(row["selected_refactoring_labels"]).split("|") if value not in rare_labels]
            if not labels:
                reasons["rows_removed_after_rare_label_filter"] += 1
                continue
            removed = len(str(row["selected_refactoring_labels"]).split("|")) - len(labels)
            reasons["rare_labels_removed"] += removed
            row["selected_refactoring_labels"] = "|".join(labels)
            row["target_text"] = " | ".join(labels)
            filtered.append(row)
        accepted = filtered

    if len(accepted) < args.minimum_records:
        raise SystemExit(
            f"Only {len(accepted)} evidence-backed training records remain; "
            f"minimum is {args.minimum_records}."
        )
    repositories = Counter(str(row["repository"]) for row in accepted)
    smells = Counter(str(row["architecture_smell"]) for row in accepted)
    labels: Counter[str] = Counter()
    label_pairs: Counter[str] = Counter()
    for row in accepted:
        row_labels = sorted(value for value in str(row["selected_refactoring_labels"]).split("|") if value)
        labels.update(row_labels)
        label_pairs.update(f"{left} + {right}" for index, left in enumerate(row_labels)
                           for right in row_labels[index + 1:])
    fingerprints = Counter(str(row["evidence_group_id"]) for row in accepted)
    commits_by_repository = Counter((str(row["repository"]), str(row["commit"])) for row in accepted)
    report = {
        "schema_version": 2,
        "input_records": raw_count,
        "accepted_records": len(accepted),
        "rejected_records": raw_count - len(accepted),
        "quality_events": dict(reasons),
        "minimum_label_support": args.minimum_label_support,
        "removed_rare_labels": sorted(rare_labels),
        "unique_commits": len({str(row["commit"]) for row in accepted}),
        "unique_repository_commits": len(commits_by_repository),
        "duplicate_repository_commit_groups": sum(count > 1 for count in commits_by_repository.values()),
        "unique_input_fingerprints": len(fingerprints),
        "repeated_input_fingerprint_groups": sum(count > 1 for count in fingerprints.values()),
        "repositories": dict(repositories),
        "architecture_smells": dict(smells),
        "refactoring_labels": dict(labels),
        "label_cooccurrence": dict(label_pairs.most_common()),
        "recommended_label_support": args.recommended_label_support,
        "labels_below_recommended_support": {
            label: count for label, count in sorted(labels.items())
            if count < args.recommended_label_support
        },
        "external_ground_truth_status": {
            "available": False,
            "note": "Cross-project labels for target systems such as Struts require an independent human-labelled dataset."
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row) + "\n" for row in accepted), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
