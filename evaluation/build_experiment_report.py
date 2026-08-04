#!/usr/bin/env python3
"""Build a machine-readable evidence report for a completed experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(repository: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repository), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if process.returncode:
        raise SystemExit(f"Git command failed: {' '.join(args)}\n{process.stdout}")
    return process.stdout.rstrip("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--model-evaluation", type=Path)
    parser.add_argument("--training-data-quality", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repository = args.repository.resolve()
    manifest = read_json(args.manifest)
    validation = read_json(args.validation)
    comparison = read_json(args.comparison)
    generated_count = int(manifest.get("status_counts", {}).get("ready_for_dry_run", 0))
    validated_candidate_count = int(validation.get("candidate_count", -1))
    if generated_count != validated_candidate_count:
        raise SystemExit(
            "Manifest/validation mismatch: "
            f"manifest has {generated_count} candidates but validation evaluated {validated_candidate_count}"
        )
    head = git(repository, "rev-parse", "HEAD")
    if head != args.version_id:
        raise SystemExit(f"Repository HEAD {head} does not match assigned revision {args.version_id}")

    status_lines = git(repository, "status", "--porcelain", "--untracked-files=all").splitlines()
    changed_files = []
    change_status = {}
    relevant_suffixes = (".java", ".kt", ".groovy", ".scala", ".xml", ".gradle", ".kts")
    for line in status_lines:
        value = line[3:].strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if not (value.endswith(relevant_suffixes) or Path(value).name in {"pom.xml", "module-info.java"}):
            continue
        changed_files.append(value)
        change_status[value] = line[:2]
    changed_files = sorted(set(changed_files))
    diff_stat = git(repository, "diff", "--numstat").splitlines()
    insertions = deletions = 0
    for line in diff_stat:
        parts = line.split("\t", 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            insertions += int(parts[0]); deletions += int(parts[1])
    for value, status in change_status.items():
        if status == "??":
            path = repository / value
            if path.is_file():
                insertions += len(path.read_text(encoding="utf-8", errors="replace").splitlines())

    with args.predictions.open(newline="", encoding="utf-8-sig") as handle:
        prediction_rows = list(csv.DictReader(handle))
    top_labels = Counter(
        row.get("suggestions", "").split("|", 1)[0].rsplit("(", 1)[0].strip()
        for row in prediction_rows if row.get("suggestions")
    )
    categories = Counter(row.get("failure_category") or "unknown" for row in validation.get("records", []))
    validated = int(validation.get("validated_count", 0))
    comparable = bool(comparison.get("aggregate_counts_comparable"))

    if validated == 0 or not changed_files:
        conclusion = "no_refactoring_applied"
        causal_claim_allowed = False
        note = "No validated source refactoring was applied; smell changes must not be attributed to refactoring."
    elif not comparable:
        conclusion = "non_comparable_smell_measurement"
        causal_claim_allowed = False
        note = "Source changed, but the before/after smell measurements are not configuration-compatible."
    else:
        conclusion = "validated_refactoring_evaluated"
        causal_claim_allowed = True
        note = "At least one build-validated refactoring was applied and measured with matched Arcan configuration."

    model_evaluation = read_json(args.model_evaluation) if args.model_evaluation and args.model_evaluation.is_file() else None
    training_quality = read_json(args.training_data_quality) if args.training_data_quality and args.training_data_quality.is_file() else None
    provenance = read_json(args.provenance) if args.provenance and args.provenance.is_file() else None
    artifacts = [args.predictions, args.manifest, args.validation, args.comparison]
    artifacts += [path for path in (args.model_evaluation, args.training_data_quality, args.provenance) if path and path.is_file()]
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "revision": {"expected": args.version_id, "actual": head, "matches": head == args.version_id},
        "predictions": {"count": len(prediction_rows), "top_label_distribution": dict(top_labels)},
        "model_evaluation": model_evaluation,
        "training_data_quality": training_quality,
        "provenance": provenance,
        "candidates": {
            "generated": generated_count,
            "validated": validated,
            "validation_failure_categories": dict(categories),
        },
        "source_change": {
            "changed_file_count": len(changed_files), "changed_files": changed_files,
            "git_status": change_status,
            "insertions": insertions, "deletions": deletions,
        },
        "smell_evaluation": {
            "matched_configuration": comparable,
            "conclusion": conclusion,
            "causal_claim_allowed": causal_claim_allowed,
            "note": note,
        },
        "artifact_sha256": {str(path): sha256(path) for path in artifacts},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
