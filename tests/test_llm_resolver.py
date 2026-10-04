from __future__ import annotations

import json
import tempfile
import unittest
import csv
import contextlib
import io
from argparse import Namespace
from pathlib import Path
from unittest import mock

from openrewrite.llm_resolver import LlmProposal, LlmResolver
from openrewrite.generate_recipes import generate
from webui.server import validate_llm_options


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": json.dumps({
            "operation": "move_class",
            "source_type": "example.left.A",
            "destination_type": "example.right.A",
            "source_signature": None,
            "confidence": 0.9,
            "reason": "selected from supplied repository symbols",
        })}}]}).encode()


class LlmResolverTests(unittest.TestCase):
    def test_openai_compatible_response_is_cached_with_provenance(self):
        with mock.patch("openrewrite.llm_resolver.urllib.request.urlopen", return_value=_Response()) as request:
            with tempfile.TemporaryDirectory() as temporary:
                resolver = LlmResolver(
                    "http://gpu-node:8000", "test-model",
                    Path(temporary), max_calls=2,
                )
                evidence = {"prediction_id": 1, "available_types": ["example.left.A"]}
                first = resolver.resolve(1, evidence)
                second = resolver.resolve(1, evidence)
                self.assertEqual(first, second)
                self.assertEqual(first.operation, "move_class")
                self.assertEqual(request.call_count, 1)
                artifact = json.loads((Path(temporary) / "prediction-0001.json").read_text())
                self.assertEqual(artifact["model"], "test-model")
                self.assertEqual(artifact["status"], "complete")

    def test_low_confidence_proposal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = LlmResolver("http://unused", "test", Path(temporary), min_confidence=0.8)
            self.assertIsNone(resolver._proposal({
                "operation": "move_class", "confidence": 0.5,
                "source_type": "a.A", "destination_type": "b.A",
            }))

    def test_dashboard_llm_options_are_bounded(self):
        enabled, endpoint, model, calls, confidence = validate_llm_options({
            "llmEnabled": True, "llmEndpoint": "http://node1:8000/",
            "llmModel": "local-model", "llmMaxCalls": 25,
            "llmMinConfidence": 0.7,
        })
        self.assertTrue(enabled)
        self.assertEqual(endpoint, "http://node1:8000")
        self.assertEqual((model, calls, confidence), ("local-model", 25, 0.7))

    def test_generator_accepts_only_a_repository_bounded_internal_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            for package, name in (("example.left", "A"), ("example.right", "B")):
                path = repository / "module/src/main/java" / Path(*package.split(".")) / f"{name}.java"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"package {package};\nclass {name} {{}}\n", encoding="utf-8")
            predictions = root / "predictions.csv"
            with predictions.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "architecture_smell", "affected_elements", "suggestions",
                ])
                writer.writeheader()
                writer.writerow({"architecture_smell": "Cyclic Dependency",
                                 "affected_elements": "example.left|example.right",
                                 "suggestions": "Move Class (0.8)"})
            proposal = LlmProposal("move_class", "example.left.A", "example.right.A",
                                   None, 0.91, "repository evidence")
            with mock.patch.object(LlmResolver, "resolve", return_value=proposal):
                with contextlib.redirect_stdout(io.StringIO()):
                    generate(Namespace(
                        repository=repository, predictions=predictions, output_dir=root / "out",
                        smell_column="architecture_smell", elements_column="affected_elements",
                        suggestions_column="suggestions", elements_separator="|",
                        severity_categories="high,medium,low", semantic_analysis=None,
                        llm_endpoint="http://gpu-node:8000", llm_model="test-model",
                        llm_timeout=10, llm_max_calls=2, llm_min_confidence=0.65,
                    ))
            manifest = json.loads((root / "out/manifest.json").read_text())
            record = manifest["records"][0]
            self.assertEqual(record["status"], "ready_for_dry_run")
            self.assertEqual(record["analysis_source"], "llm_bounded")
            self.assertEqual(record["candidate_origin"], "llm_bounded_selection")
            self.assertEqual(manifest["llm_resolution"]["accepted_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
