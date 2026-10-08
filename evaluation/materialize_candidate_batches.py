#!/usr/bin/env python3
"""Materialize stable OpenRewrite candidate and prediction batches for reuse."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from evaluation.validate_openrewrite_candidates import candidate_priority
except ModuleNotFoundError:  # Direct script execution adds evaluation/, not its parent.
    from validate_openrewrite_candidates import candidate_priority


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def materialize(generated: Path, predictions: Path, output: Path, batch_size: int) -> dict:
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    manifest = json.loads((generated / "manifest.json").read_text(encoding="utf-8"))
    candidates = sorted(
        (row for row in manifest.get("records", []) if row.get("status") == "ready_for_dry_run"),
        key=candidate_priority,
    )
    with predictions.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        prediction_fields = list(reader.fieldnames or [])
        prediction_rows = list(reader)
    candidate_fields = sorted({key for row in candidates for key in row})
    output.mkdir(parents=True, exist_ok=True)
    batches = []
    for offset in range(0, len(candidates), batch_size):
        number = offset // batch_size + 1
        rows = candidates[offset:offset + batch_size]
        candidate_file = output / f"batch-{number:04d}-candidates.csv"
        write_csv(candidate_file, rows, candidate_fields)
        prediction_ids = sorted({int(row["prediction_id"]) for row in rows})
        selected_predictions = [
            row for index, row in enumerate(prediction_rows, start=1) if index in prediction_ids
        ]
        prediction_file = output / f"batch-{number:04d}-predictions.csv"
        write_csv(prediction_file, selected_predictions, prediction_fields)
        batches.append({
            "batch": number,
            "candidate_count": len(rows),
            "prediction_count": len(selected_predictions),
            "prediction_ids": prediction_ids,
            "candidate_file": candidate_file.name,
            "prediction_file": prediction_file.name,
        })
    snapshot = output / "predictions.csv"
    snapshot.write_bytes(predictions.read_bytes())
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "batch_size": batch_size,
        "candidate_count": len(candidates),
        "prediction_record_count": len(prediction_rows),
        "total_batches": len(batches),
        "predictions_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "batches": batches,
    }
    (output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--batch-size", required=True, type=int)
    args = parser.parse_args()
    result = materialize(args.generated_dir, args.predictions, args.output_dir, args.batch_size)
    print(json.dumps({key: value for key, value in result.items() if key != "batches"}, indent=2))


if __name__ == "__main__":
    main()
