from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.materialize_candidate_batches import materialize


class CandidateBatchTests(unittest.TestCase):
    def test_materializes_stable_candidate_and_prediction_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / "generated"
            generated.mkdir()
            records = [
                {"candidate_id": "p3-c1", "prediction_id": 3, "status": "ready_for_dry_run",
                 "severity": "medium", "severity_score": 3, "model_rank": 1,
                 "model_score": 0.8, "api_impact": "internal_only"},
                {"candidate_id": "p1-c1", "prediction_id": 1, "status": "ready_for_dry_run",
                 "severity": "high", "severity_score": 5, "model_rank": 1,
                 "model_score": 0.9, "api_impact": "internal_only"},
                {"candidate_id": "p2-c1", "prediction_id": 2, "status": "unresolved",
                 "severity": "high"},
            ]
            (generated / "manifest.json").write_text(
                json.dumps({"records": records}), encoding="utf-8"
            )
            predictions = root / "predictions.csv"
            with predictions.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["architecture_smell", "suggestions"])
                writer.writeheader()
                for number in range(1, 4):
                    writer.writerow({"architecture_smell": f"smell-{number}", "suggestions": "Move Class"})

            result = materialize(generated, predictions, root / "batches", batch_size=1)

            self.assertEqual(result["total_batches"], 2)
            self.assertEqual(result["batches"][0]["prediction_ids"], [1])
            self.assertEqual(result["batches"][1]["prediction_ids"], [3])
            with (root / "batches/batch-0001-predictions.csv").open() as handle:
                first = list(csv.DictReader(handle))
            self.assertEqual(first[0]["architecture_smell"], "smell-1")
            self.assertTrue((root / "batches/predictions.csv").is_file())


if __name__ == "__main__":
    unittest.main()
