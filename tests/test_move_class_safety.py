from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from openrewrite.generate_recipes import generate


class MoveClassSafetyTests(unittest.TestCase):
    def run_generator(self, left_path: str, right_path: str, extra: dict[str, str] | None = None,
                      prediction_count: int = 1):
        temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name); repo = root / "repo"
        files = {
            left_path: "package example.left;\nimport example.right.B;\npublic class A {}",
            right_path: "package example.right;\nimport example.left.A;\npublic class B {}",
            **(extra or {}),
        }
        for relative, source in files.items():
            path = repo / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(source)
        predictions = root / "predictions.csv"
        with predictions.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["architecture_smell", "affected_elements", "suggestions"])
            writer.writeheader()
            for _ in range(prediction_count):
                writer.writerow({"architecture_smell": "Cyclic Dependency",
                                 "affected_elements": "example.left|example.right",
                                 "suggestions": "Move Class (0.8)"})
        output = root / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            generate(Namespace(repository=repo, predictions=predictions, output_dir=output,
                smell_column="architecture_smell", elements_column="affected_elements",
                suggestions_column="suggestions", elements_separator="|",
                severity_categories="high,medium,low", semantic_analysis=None))
        return temporary, json.loads((output / "manifest.json").read_text())

    def test_cross_module_moves_are_rejected(self):
        temporary, manifest = self.run_generator(
            "left/src/main/java/example/left/A.java", "right/src/main/java/example/right/B.java")
        try: self.assertEqual(manifest["records"][0]["status"], "unsafe_destination")
        finally: temporary.cleanup()

    def test_production_to_test_moves_are_rejected(self):
        temporary, manifest = self.run_generator(
            "module/src/main/java/example/left/A.java", "module/src/test/java/example/right/B.java")
        try: self.assertEqual(manifest["records"][0]["status"], "unsafe_destination")
        finally: temporary.cleanup()

    def test_destination_type_conflict_is_rejected(self):
        temporary, manifest = self.run_generator(
            "module/src/main/java/example/left/A.java", "module/src/main/java/example/right/B.java",
            {"module/src/main/java/example/right/A.java": "package example.right; public class A {}"})
        try:
            record = manifest["records"][0]
            self.assertNotEqual(record.get("destination_type"), "example.right.A")
        finally: temporary.cleanup()

    def test_multiple_predictions_never_reuse_a_source_class(self):
        temporary, manifest = self.run_generator(
            "module/src/main/java/example/left/A.java", "module/src/main/java/example/right/B.java",
            prediction_count=2)
        try:
            sources = [row["source_type"] for row in manifest["records"] if row["status"] == "ready_for_dry_run"]
            self.assertEqual(len(sources), len(set(sources)))
        finally: temporary.cleanup()


if __name__ == "__main__": unittest.main()
