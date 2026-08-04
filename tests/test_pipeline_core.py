from __future__ import annotations

import csv
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from evaluation.summarize_arcan import comparison
from evaluation.validate_openrewrite_candidates import classify_failure, has_compatibility_strategy
from openrewrite.generate_recipes import generate, ranked_suggestions
from webui.server import detect_stage


class SuggestionTests(unittest.TestCase):
    def test_ranked_suggestions_preserve_rank_and_score(self) -> None:
        self.assertEqual(
            ranked_suggestions("Extract Method (0.610) | Move Class (0.520)"),
            [("Extract Method", 0.61), ("Move Class", 0.52)],
        )

    def test_generator_uses_supported_lower_rank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            for package, name, imported in (
                ("example.left", "Left", "example.right.Right"),
                ("example.right", "Right", "example.left.Left"),
            ):
                path = repository / "module" / "src" / "main" / "java" / Path(*package.split(".")) / f"{name}.java"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"package {package};\nimport {imported};\npublic class {name} {{}}\n")
            predictions = root / "predictions.csv"
            with predictions.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["architecture_smell", "affected_elements", "suggestions"])
                writer.writeheader()
                writer.writerow({
                    "architecture_smell": "Cyclic Dependency",
                    "affected_elements": "example.left|example.right",
                    "suggestions": "Extract Method (0.7) | Move Class (0.6)",
                })
            output = root / "output"
            with contextlib.redirect_stdout(io.StringIO()):
                generate(Namespace(
                    repository=repository, predictions=predictions, output_dir=output,
                    smell_column="architecture_smell", elements_column="affected_elements",
                    suggestions_column="suggestions", elements_separator="|", max_candidates=40,
                ))
            manifest = json.loads((output / "manifest.json").read_text())
            record = manifest["records"][0]
            self.assertEqual(record["status"], "ready_for_dry_run")
            self.assertEqual(record["model_rank"], 2)
            self.assertEqual(record["model_score"], 0.6)


class EvidenceTests(unittest.TestCase):
    def test_arcan_comparison_requires_matching_configuration(self) -> None:
        base = {"version": "1.2.1", "analysis_configuration": "a",
                "package_cycle_members": [], "class_cycle_members": []}
        result = comparison(base, {**base, "analysis_configuration": "b"})
        self.assertFalse(result["aggregate_counts_comparable"])
        self.assertIsNotNone(result["comparison_warning"])

    def test_failure_classifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "verify.log"
            log.write_text("[ERROR] COMPILATION FAILURE: cannot find symbol")
            category, _ = classify_failure(log, "verification failed")
            self.assertEqual(category, "compilation")

    def test_public_api_requires_explicit_compatibility_strategy(self) -> None:
        ordinary = {"source_type": "example.PublicApi"}
        supported = {"source_type": "org.apache.logging.log4j.core.appender.rolling.FileSize"}
        self.assertFalse(has_compatibility_strategy(ordinary, "generic"))
        self.assertTrue(has_compatibility_strategy(supported, "log4j2"))

    def test_stage_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "pipeline.log"
            log.write_text("Stage: baseline\nwork\nStage: OpenRewrite\n")
            self.assertEqual(detect_stage(log), "rewrite")

    def test_stage_marker_overrides_verbose_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "pipeline.log"
            marker = Path(temporary) / ".pipeline-stage"
            log.write_text("Stage: baseline\n" + "verbose output\n" * 1000)
            marker.write_text("Stage: final verification\n")
            self.assertEqual(detect_stage(log, marker), "final_verify")


if __name__ == "__main__":
    unittest.main()
