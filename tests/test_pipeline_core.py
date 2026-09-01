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
from unittest import mock

from evaluation.summarize_arcan import comparison, cycles
from evaluation.validate_openrewrite_candidates import classify_failure, has_compatibility_strategy
from openrewrite.generate_recipes import classify_severity, generate, ranked_suggestions
from webui.server import (detect_stage, normalize_slurm_state, read_json_file,
                          validate_batch_options, validate_max_commits,
                          validate_stop_stage)
import webui.server as dashboard


class SuggestionTests(unittest.TestCase):
    def test_severity_uses_smell_type_and_scope(self) -> None:
        self.assertEqual(classify_severity("Cyclic Dependency", 8)[0], "high")
        self.assertEqual(classify_severity("Unstable Dependency", 2)[0], "medium")
        self.assertEqual(classify_severity("Other", 1)[0], "low")

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
                    suggestions_column="suggestions", elements_separator="|",
                ))
            manifest = json.loads((output / "manifest.json").read_text())
            record = manifest["records"][0]
            self.assertEqual(record["status"], "ready_for_dry_run")
            self.assertEqual(record["model_rank"], 2)
            self.assertEqual(record["model_score"], 0.6)


class EvidenceTests(unittest.TestCase):
    def test_arcan_cycles_are_canonicalized_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cycles.csv"
            path.write_text(
                "cycle,A,B,C\nfirst,1,1,0\nsecond,1,1,0\nthird,0,1,1\n",
                encoding="utf-8",
            )
            self.assertEqual(cycles(path), [["A", "B"], ["B", "C"]])

    def test_arcan_comparison_requires_matching_configuration(self) -> None:
        base = {"version": "1.2.1", "analysis_configuration": "a",
                "package_cycle_members": [], "class_cycle_members": []}
        result = comparison(base, {**base, "analysis_configuration": "b"})
        self.assertFalse(result["aggregate_counts_comparable"])
        self.assertIsNotNone(result["comparison_warning"])

    def test_arcan_comparison_checks_compiled_class_population(self) -> None:
        base = {
            "version": "1.2.1", "analysis_configuration": "a",
            "compiled_classes": 100, "compiled_class_paths": [f"C{i}.class" for i in range(100)],
            "package_cycle_members": [], "class_cycle_members": [],
        }
        compatible = comparison(base, {
            **base, "compiled_classes": 101,
            "compiled_class_paths": [*base["compiled_class_paths"], "Added.class"],
        })
        self.assertTrue(compatible["aggregate_counts_comparable"])
        self.assertEqual(compatible["population_comparison"]["difference"], 1)
        incompatible = comparison(base, {
            **base, "compiled_classes": 120,
            "compiled_class_paths": [f"Other{i}.class" for i in range(120)],
        })
        self.assertFalse(incompatible["aggregate_counts_comparable"])
        self.assertIn("compiled-class populations differ", incompatible["comparison_warning"])

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

    def test_slurm_states_are_normalized_for_the_dashboard(self) -> None:
        self.assertEqual(normalize_slurm_state("PENDING"), "queued")
        self.assertEqual(normalize_slurm_state("RUNNING"), "running")
        self.assertEqual(normalize_slurm_state("COMPLETED"), "completed")
        self.assertEqual(normalize_slurm_state("CANCELLED by 1000"), "stopped")
        self.assertEqual(normalize_slurm_state("OUT_OF_MEMORY"), "failed")

    def test_hpc_batch_options_are_validated(self) -> None:
        categories, size, start, maximum = validate_batch_options({
            "severityCategories": ["high"], "batchSize": "12",
            "startBatch": "2", "maxBatches": "3",
        })
        self.assertEqual((categories, size, start, maximum), (["high"], 12, 2, 3))
        with self.assertRaises(ValueError):
            validate_batch_options({"severityCategories": ["critical"]})

    def test_pipeline_final_task_is_validated(self) -> None:
        self.assertEqual(validate_stop_stage({}), "summary")
        self.assertEqual(validate_stop_stage({"stopStage": "training"}), "training")
        with self.assertRaises(ValueError):
            validate_stop_stage({"stopStage": "unknown"})
        with self.assertRaises(ValueError):
            validate_stop_stage({"stopStage": "baseline"}, "rewrite")

    def test_mining_commit_limit_is_validated(self) -> None:
        self.assertEqual(validate_max_commits({}), 500)
        self.assertEqual(validate_max_commits({"maxCommitsPerRepository": "2000"}), 2000)
        for value in (0, 10001, "not-a-number"):
            with self.assertRaises(ValueError):
                validate_max_commits({"maxCommitsPerRepository": value})

    def test_experiment_name_is_normalized_and_validated(self) -> None:
        self.assertEqual(dashboard.validate_run_name({"runName": "  Log4j2   evaluation  "}),
                         "Log4j2 evaluation")
        for value in ("", "   ", "x" * 81):
            with self.assertRaises(ValueError):
                dashboard.validate_run_name({"runName": value})

    def test_pretrained_model_directory_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "final_model"
            model.mkdir()
            for name in ("config.json", "labels.json", "model.safetensors", "tokenizer.json"):
                (model / name).write_text("{}")
            self.assertEqual(dashboard.validate_pretrained_model(
                {"pretrainedModelDir": str(model)}), str(model.resolve()))
            (model / "labels.json").unlink()
            with self.assertRaises(ValueError):
                dashboard.validate_pretrained_model({"pretrainedModelDir": str(model)})

    def test_default_shared_model_is_used_without_user_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "shared" / "trained-model" / "default" / "final_model"
            model.mkdir(parents=True)
            for name in ("config.json", "labels.json", "model.safetensors", "tokenizer.json"):
                (model / name).write_text("{}")
            with mock.patch.object(dashboard, "DEFAULT_TRAINED_MODEL", model):
                self.assertEqual(dashboard.validate_pretrained_model({}), str(model.resolve()))

    def test_concatenated_metadata_recovers_latest_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "metadata.json"
            path.write_text('{"status":"queued"}\n{"status":"running","id":"newest"}\n')
            self.assertEqual(read_json_file(path)["id"], "newest")
            self.assertEqual(json.loads(path.read_text())["status"], "running")

    def test_historical_hpc_runs_are_rediscovered_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "hpc-runs" / "logging-log4j2" / "12345"
            results = run_root / "results"
            results.mkdir(parents=True)
            (run_root / ".pipeline-stage").write_text("Stage: summary\n")
            (results / "run-provenance.json").write_text(json.dumps({
                "created_at": "2026-08-05T20:00:00+00:00",
                "project": "logging-log4j2",
                "repository_url": "https://github.com/apache/logging-log4j2.git",
                "version_id": "4f474b32751f4ccad67424ca585612584440cd63",
            }))
            (results / "experiment-report.json").write_text("{}")
            state_runs = root / "state" / "runs"
            with (mock.patch.object(dashboard, "RUNS", state_runs),
                  mock.patch.object(dashboard, "STATE", root / "state"),
                  mock.patch.object(dashboard, "HPC_RUNS_ROOT", root / "hpc-runs"),
                  mock.patch.object(dashboard, "EXECUTION_MODE", "hpc")):
                dashboard.discover_hpc_runs()
                metadata = read_json_file(
                    state_runs / "historical-logging-log4j2-12345" / "metadata.json")
            self.assertEqual(metadata["status"], "completed")
            self.assertEqual(metadata["logicalRunId"], "12345")
            self.assertTrue(metadata["discovered"])

    def test_hpc_submission_exports_inputs_without_running_pipeline_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs = root / "state" / "runs"
            predictions = root / "predictions.csv"
            predictions.write_text("architecture_smell,suggestions\ncycle,Move Class\n")
            csvs = []
            for name in ("component-metrics.csv", "smell-characteristics.csv", "smell-affects.csv"):
                content = f"project,versionId\nlogging-log4j2,4f474b3\n"
                csvs.append({"name": name, "data": __import__("base64").b64encode(content.encode()).decode()})
            completed = subprocess.CompletedProcess(["sbatch"], 0, stdout="12345\n", stderr="")
            with (mock.patch.object(dashboard, "RUNS", runs),
                  mock.patch.object(dashboard, "STATE", root / "state"),
                  mock.patch.object(dashboard, "HPC_PROJECT_SPACE", root / "scratch"),
                  mock.patch.object(dashboard, "HPC_RUNS_ROOT", root / "scratch" / "runs"),
                  mock.patch.object(dashboard, "EXECUTION_MODE", "hpc"),
                  mock.patch.object(dashboard, "hpc_available", return_value=True),
                  mock.patch.object(dashboard, "latest_compatible_predictions", return_value=predictions),
                  mock.patch.object(dashboard.subprocess, "run", return_value=completed) as submit):
                result = dashboard.start_hpc_run({
                    "runName": "Log4j2 prediction check",
                    "system": "logging-log4j2", "repositoryUrl": "https://example.test/repo.git",
                    "versionId": "4f474b3", "mode": "latest_predictions",
                    "baselineFiles": csvs, "severityCategories": ["high"],
                    "batchSize": 10, "startBatch": 1, "maxBatches": 1,
                })
            self.assertEqual(result["slurmJobId"], "12345")
            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["executionTarget"], "hpc")
            self.assertEqual(result["runName"], "Log4j2 prediction check")
            self.assertEqual(submit.call_args.kwargs["env"]["PIPELINE_MODE"], "reuse_predictions")
            self.assertEqual(submit.call_args.kwargs["env"]["STOP_STAGE"], "summary")
            self.assertEqual(submit.call_args.kwargs["env"]["MAX_COMMITS_PER_REPO"], "500")


if __name__ == "__main__":
    unittest.main()
