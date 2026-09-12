from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import openrewrite.generate_recipes as recipe_generator
from openrewrite.generate_recipes import generate
from evaluation.validate_openrewrite_candidates import candidate_priority


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

    def test_move_with_original_package_type_dependency_is_rejected(self):
        temporary, manifest = self.run_generator(
            "module/src/main/java/example/left/A.java",
            "module/src/main/java/example/right/B.java",
            {
                "module/src/main/java/example/left/A.java":
                    "package example.left;\nimport example.right.B;\npublic class A { Helper helper; }",
                "module/src/main/java/example/left/Helper.java":
                    "package example.left; public class Helper {}",
                "module/src/main/java/example/right/B.java":
                    "package example.right;\nimport example.left.A;\npublic class B { Peer peer; }",
                "module/src/main/java/example/right/Peer.java":
                    "package example.right; public class Peer {}",
            },
        )
        try:
            record = manifest["records"][0]
            self.assertEqual(record["status"], "unsafe_destination")
            self.assertIn("original-package types", record["reason"])
            self.assertRegex(record["reason"], r"example\.(left\.Helper|right\.Peer)")
        finally:
            temporary.cleanup()

    def test_move_referenced_by_service_metadata_is_rejected(self):
        temporary, manifest = self.run_generator(
            "module/src/main/java/example/left/A.java",
            "module/src/main/java/example/right/B.java",
            {
                "module/src/test/resources/META-INF/services/example.left.A":
                    "example.left.AProvider\n",
                "module/src/test/resources/META-INF/services/example.right.B":
                    "example.right.BProvider\n",
            },
        )
        try:
            record = manifest["records"][0]
            self.assertEqual(record["status"], "unsafe_metadata_reference")
            self.assertIn("META-INF/services/example.", record["reason"])
        finally:
            temporary.cleanup()

    def test_metadata_files_are_loaded_once_for_all_predictions(self):
        original = recipe_generator.external_metadata_corpus
        with mock.patch.object(
            recipe_generator, "external_metadata_corpus", wraps=original
        ) as corpus:
            temporary, _ = self.run_generator(
                "module/src/main/java/example/left/A.java",
                "module/src/main/java/example/right/B.java",
                prediction_count=20,
            )
        try:
            self.assertEqual(corpus.call_count, 1)
        finally:
            temporary.cleanup()

    def test_internal_candidates_are_validated_before_public_candidates(self):
        public = {"prediction_id": 1, "severity": "high", "severity_score": 5,
                  "api_impact": "public_class"}
        internal = {"prediction_id": 2, "severity": "medium", "severity_score": 3,
                    "api_impact": "internal_only"}
        self.assertEqual(sorted([public, internal], key=candidate_priority)[0], internal)

    def test_curated_evidence_is_executed_in_first_batch(self):
        internal = {"prediction_id": 1, "severity": "high", "severity_score": 5,
                    "api_impact": "internal_only", "candidate_origin": "model_prediction"}
        curated = {"prediction_id": 999, "severity": "high", "severity_score": 5,
                   "api_impact": "public_class", "candidate_origin": "curated_evidence"}
        self.assertEqual(sorted([internal, curated], key=candidate_priority)[0], curated)

    def test_manifest_classifies_public_move_class_api_impact(self):
        temporary, manifest = self.run_generator(
            "module/src/main/java/example/left/A.java",
            "module/src/main/java/example/right/B.java",
        )
        try:
            record = manifest["records"][0]
            self.assertEqual(record["api_impact"], "public_class")
            self.assertEqual(record["compatibility_strategy"], "required")
            self.assertFalse(record["automatic_execution_allowed"])
            self.assertEqual(record["status"], "unsafe_public_api")
            self.assertIn("no safe internal candidate", record["reason"])
        finally:
            temporary.cleanup()

    def test_public_test_source_is_classified_separately_from_production_api(self):
        temporary, manifest = self.run_generator(
            "module/src/test/java/example/left/A.java",
            "module/src/test/java/example/right/B.java",
        )
        try:
            record = manifest["records"][0]
            self.assertEqual(record["status"], "ready_for_dry_run")
            self.assertEqual(record["api_impact"], "test_only")
            self.assertTrue(record["automatic_execution_allowed"])
        finally:
            temporary.cleanup()

    def test_internal_source_is_selected_ahead_of_public_ranked_source(self):
        temporary, manifest = self.run_generator(
            "module/src/main/java/example/left/A.java",
            "module/src/main/java/example/right/B.java",
            {
                "module/src/main/java/example/right/B.java":
                    "package example.right;\nimport example.left.A;\nclass B {}",
            },
        )
        try:
            record = manifest["records"][0]
            self.assertEqual(record["status"], "ready_for_dry_run")
            self.assertEqual(record["source_type"], "example.right.B")
            self.assertEqual(record["api_impact"], "internal_only")
        finally:
            temporary.cleanup()

    def test_one_way_dependency_can_supply_safe_internal_candidate(self):
        temporary, manifest = self.run_generator(
            "module/src/main/java/example/left/A.java",
            "module/src/main/java/example/right/B.java",
            {"module/src/main/java/example/right/B.java": "package example.right; class B {}"},
        )
        try:
            record = manifest["records"][0]
            self.assertEqual(record["status"], "ready_for_dry_run")
            self.assertEqual(record["source_type"], "example.right.B")
            self.assertIn("one-way dependency candidate", record["reason"])
        finally:
            temporary.cleanup()

    def test_curated_filesize_is_labelled_and_executable(self):
        temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name)
        repo = root / "repo"
        files = {
            "log4j-core/src/main/java/org/apache/logging/log4j/core/appender/rolling/FileSize.java":
                "package org.apache.logging.log4j.core.appender.rolling; public final class FileSize {}",
            "log4j-core/src/main/java/org/apache/logging/log4j/core/appender/rolling/action/Action.java":
                "package org.apache.logging.log4j.core.appender.rolling.action; public interface Action {}",
        }
        for relative, source in files.items():
            path = repo / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(source)
        predictions = root / "predictions.csv"
        predictions.write_text(
            "architecture_smell,affected_elements,suggestions\n"
            "Cyclic Dependency,org.apache.logging.log4j.core.appender.rolling|org.apache.logging.log4j.core.appender.rolling.action,Extract Method (0.7)\n"
        )
        output = root / "out"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                generate(Namespace(repository=repo, predictions=predictions, output_dir=output,
                    smell_column="architecture_smell", elements_column="affected_elements",
                    suggestions_column="suggestions", elements_separator="|",
                    severity_categories="high,medium,low", semantic_analysis=None,
                    include_curated_filesize=True))
            records = json.loads((output / "manifest.json").read_text())["records"]
            record = next(row for row in records if row["candidate_origin"] == "curated_evidence")
            self.assertEqual(record["candidate_origin"], "curated_evidence")
            self.assertEqual(record["compatibility_strategy"], "file_size_facade")
            self.assertTrue(record["automatic_execution_allowed"])
            self.assertTrue((output / record["recipe_file"]).is_file())
        finally:
            temporary.cleanup()


if __name__ == "__main__": unittest.main()
