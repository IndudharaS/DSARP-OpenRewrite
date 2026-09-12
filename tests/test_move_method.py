from __future__ import annotations

import unittest
import csv
import contextlib
import io
import json
import tempfile
from argparse import Namespace
from pathlib import Path

from openrewrite.candidate_models import JavaMethod, MethodDependency
from openrewrite.generate_recipes import generate
from openrewrite.resolvers.move_method import resolve_move_method


def method(**overrides) -> JavaMethod:
    values = dict(
        qualified_owner="example.left.A", name="helper", signature="helper(example.right.B)",
        return_type="void", parameters=("example.right.B",), path="module/src/main/java/example/left/A.java",
        package="example.left", module="module", source_set="main", visibility="public",
        is_static=True, is_abstract=False, is_native=False, is_synchronized=False,
        source_state_references=0,
        dependencies=(MethodDependency("example.right.B", "example.right", "method_call", 4),),
    )
    values.update(overrides)
    return JavaMethod(**values)


class MoveMethodResolverTests(unittest.TestCase):
    def resolve(self, methods):
        return resolve_move_method(methods, {"example.left", "example.right"},
                                   {"example.left.A", "example.right.B"}, set())

    def test_strong_affinity_selects_exact_candidate(self):
        candidate = self.resolve([method()])
        self.assertEqual(candidate.status, "ready_for_dry_run")
        self.assertEqual(candidate.destination_class, "example.right.B")
        self.assertEqual(candidate.structural_score, 1.0)

    def test_weak_affinity_is_unresolved(self):
        dependencies = (
            MethodDependency("example.right.B", "example.right", "method_call", 2),
            MethodDependency("outside.C", "outside", "method_call", 8),
        )
        self.assertEqual(self.resolve([method(dependencies=dependencies)]).status, "unresolved_destination")

    def test_source_state_heavy_method_is_rejected(self):
        self.assertEqual(self.resolve([method(source_state_references=10)]).status, "unsafe_source_state")

    def test_destination_conflict_is_rejected(self):
        target = method(qualified_owner="example.right.B", package="example.right")
        self.assertEqual(self.resolve([method(), target]).status, "destination_conflict")

    def test_non_static_and_non_public_methods_are_not_executable(self):
        self.assertIsNone(self.resolve([method(is_static=False)]))
        self.assertEqual(self.resolve([method(visibility="private")]).status, "unsupported_visibility")

    def test_duplicate_method_is_not_selected(self):
        claimed = {("example.left.A", "helper(example.right.B)")}
        self.assertIsNone(resolve_move_method([method()], {"example.left", "example.right"},
                                             {"example.left.A", "example.right.B"}, claimed))

    def test_executable_method_is_preferred_over_stronger_unsafe_method(self):
        unsafe = method(name="unsafe", signature="unsafe(example.right.B)",
                        source_state_references=1,
                        dependencies=(MethodDependency("example.right.B", "example.right", "method_call", 20),))
        safe = method(name="safe", signature="safe(example.right.B)",
                      dependencies=(MethodDependency("example.right.B", "example.right", "method_call", 3),))
        candidate = self.resolve([unsafe, safe])
        self.assertEqual(candidate.status, "ready_for_dry_run")
        self.assertEqual(candidate.source_member, "safe")


class MoveMethodManifestTests(unittest.TestCase):
    def generate(self, target_path="module/src/main/java/example/right/B.java"):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository = root / "repo"
        sources = {
            "module/src/main/java/example/left/A.java": "package example.left; public class A { public static void helper(example.right.B b) { b.run(); } }",
            target_path: "package example.right; public class B { public void run() {} }",
        }
        for relative, text in sources.items():
            path = repository / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        predictions = root / "predictions.csv"
        with predictions.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["architecture_smell", "affected_elements", "suggestions"])
            writer.writeheader(); writer.writerow({"architecture_smell": "Cyclic Dependency",
                "affected_elements": "example.left|example.right",
                "suggestions": "Extract Method (0.81) | Move Method (0.72)"})
        semantic = root / "semantic.json"
        semantic.write_text(json.dumps({"methods": [{
            "sourceClass": "example.left.A", "sourceMethod": "helper",
            "signature": "helper(example.right.B)", "sourcePackage": "example.left",
            "path": "module/src/main/java/example/left/A.java", "module": "module", "sourceSet": "main",
            "visibility": "public", "static": True, "abstract": False, "native": False,
            "synchronized": False, "sourceStateReferences": 0,
            "dependencies": [{"targetType": "example.right.B", "targetPackage": "example.right",
                              "kind": "method_call", "count": 3}],
        }]}))
        output = root / "output"
        with contextlib.redirect_stdout(io.StringIO()):
            generate(Namespace(repository=repository, predictions=predictions, output_dir=output,
                smell_column="architecture_smell", elements_column="affected_elements",
                suggestions_column="suggestions", elements_separator="|",
                severity_categories="high,medium,low", semantic_analysis=semantic))
        return temporary, output

    def test_manifest_and_yaml_preserve_model_evidence(self):
        temporary, output = self.generate()
        try:
            record = json.loads((output / "manifest.json").read_text())["records"][0]
            self.assertEqual(record["status"], "ready_for_dry_run")
            self.assertEqual(record["refactoring_kind"], "Move Method")
            self.assertEqual(record["model_rank"], 2)
            self.assertEqual(record["model_score"], 0.72)
            self.assertEqual(record["analysis_source"], "semantic")
            self.assertEqual(record["api_impact"], "public_method")
            self.assertEqual(record["compatibility_strategy"], "required")
            self.assertFalse(record["automatic_execution_allowed"])
            recipe = (output / record["recipe_file"]).read_text()
            self.assertIn("dsarp.rewrite.MoveMethod", recipe)
            self.assertIn("methodPattern: helper(example.right.B)", recipe)
        finally:
            temporary.cleanup()

    def test_cross_module_destination_is_rejected(self):
        temporary, output = self.generate("other/src/main/java/example/right/B.java")
        try:
            record = json.loads((output / "manifest.json").read_text())["records"][0]
            self.assertEqual(record["status"], "cross_module")
            self.assertIsNone(record["recipe_file"])
        finally:
            temporary.cleanup()

    def test_production_to_test_destination_is_rejected(self):
        temporary, output = self.generate("module/src/test/java/example/right/B.java")
        try:
            record = json.loads((output / "manifest.json").read_text())["records"][0]
            self.assertEqual(record["status"], "test_boundary")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
