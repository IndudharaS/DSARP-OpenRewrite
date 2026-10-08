from __future__ import annotations

import csv
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from evaluation.summarize_arcan import comparison, cycles
from evaluation.validate_openrewrite_candidates import (affected_maven_modules,
                                                         changed_test_selectors,
                                                         classify_failure,
                                                         has_compatibility_strategy,
                                                         maven_command)
from openrewrite.generate_recipes import classify_severity, generate, ranked_suggestions
from openrewrite.compatibility_profiles import profile_for_target
from webui.server import (detect_stage, normalize_slurm_state, read_json_file,
                          result_summary,
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
                path.write_text(f"package {package};\nimport {imported};\nclass {name} {{}}\n")
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
    def test_compatibility_profile_is_selected_by_system_or_repository(self) -> None:
        self.assertEqual(profile_for_target("logging-log4j2", "https://example.test/repo.git"),
                         "log4j2")
        self.assertEqual(profile_for_target(
            "custom", "https://github.com/apache/logging-log4j2.git/"), "log4j2")
        self.assertEqual(profile_for_target(
            "tika", "https://github.com/apache/tika.git"), "none")

    def test_generic_cli_uses_repository_profile_auto_detection(self) -> None:
        script = (Path(__file__).parents[1] / "scripts" / "run_generic_pipeline.sh").read_text()
        self.assertIn("profile_for_target", script)
        self.assertIn('if [[ "$detected_profile" == "none" ]]', script)

    def test_production_changes_map_to_affected_maven_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "pom.xml").write_text("<project/>", encoding="utf-8")
            module = repository / "module-a"
            module.mkdir()
            (module / "pom.xml").write_text("<project/>", encoding="utf-8")
            self.assertEqual(
                affected_maven_modules(repository, [
                    "module-a/src/main/java/example/Service.java",
                ]),
                ["module-a"],
            )

    def test_changed_java_tests_become_surefire_selectors(self) -> None:
        self.assertEqual(changed_test_selectors([
            "module/src/test/java/example/ServiceLoaderTest.java",
            "module/src/main/java/example/Service.java",
        ]), ["example.ServiceLoaderTest"])

    def test_run_summary_never_displays_an_external_prediction_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "run"
            external = root / "cached-predictions.csv"
            external.write_text("architecture_smell,suggestions\ncycle,Move Class\n")
            data = {
                "runRoot": str(run_root), "system": "tika", "versionId": "abc",
                "command": ["pipeline", "--predictions-csv", str(external)],
            }
            with mock.patch.object(dashboard, "PIPELINE_CACHE", root / "cache"):
                self.assertNotIn("predictions", result_summary(data))
            current = run_root / "pipeline-results" / "tika_refactoring_suggestions_from_trained_model.csv"
            current.parent.mkdir(parents=True)
            current.write_text("architecture_smell,suggestions\ncycle,Move Class\n")
            summary = result_summary(data)
            self.assertEqual(summary["predictions"]["count"], 1)
            self.assertEqual(summary["predictions"]["origin"], "current_run")

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

    def test_arcan_compares_smell_instances_not_only_counts(self) -> None:
        base = {
            "version": "1.2.1", "analysis_configuration": "a",
            "compiled_class_paths": ["example/A.class"],
            "package_cycle_members": [["a", "b"]], "class_cycle_members": [],
            "hub_like_dependency_instances": ["old"],
            "unstable_dependency_instances": [],
        }
        result = comparison(base, {
            **base, "package_cycle_members": [["b", "c"]],
            "hub_like_dependency_instances": ["new"],
        })
        self.assertEqual(result["instance_comparison"]["package_cycles"]["resolved"], [["a", "b"]])
        self.assertEqual(result["instance_comparison"]["package_cycles"]["introduced"], [["b", "c"]])
        self.assertEqual(result["instance_comparison"]["hub_like_dependencies"]["resolved"], ["old"])

    def test_arcan_population_uses_validated_move_contract(self) -> None:
        before = {
            "version": "1.2.1", "analysis_configuration": "a",
            "compiled_class_paths": ["module/example/left/A.class"],
            "package_cycle_members": [], "class_cycle_members": [],
        }
        candidate = {"records": [{
            "validation_status": "validated", "refactoring_kind": "Move Class",
            "source_type": "example.left.A", "destination_type": "example.right.A",
            "compatibility_strategy": "not_required",
        }]}
        expected = comparison(before, {
            **before, "compiled_class_paths": ["module/example/right/A.class"],
        }, candidate)
        self.assertTrue(expected["aggregate_counts_comparable"])
        unexpected = comparison(before, {
            **before, "compiled_class_paths": ["module/example/right/A.class", "Other.class"],
        }, candidate)
        self.assertFalse(unexpected["aggregate_counts_comparable"])
        self.assertEqual(unexpected["population_comparison"]["unexpected_added_class_paths"],
                         ["Other.class"])

    def test_failure_classifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "verify.log"
            log.write_text("[ERROR] COMPILATION FAILURE: cannot find symbol")
            category, _ = classify_failure(log, "verification failed")
            self.assertEqual(category, "compilation")

    def test_public_api_requires_explicit_compatibility_strategy(self) -> None:
        ordinary = {"source_type": "example.PublicApi"}
        supported = {
            "source_type": "org.apache.logging.log4j.core.appender.rolling.FileSize",
            "destination_type": "org.apache.logging.log4j.core.appender.rolling.action.FileSize",
        }
        self.assertFalse(has_compatibility_strategy(ordinary, "none"))
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

    def test_completed_pipeline_log_recovers_missing_slurm_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "slurm.log"
            log.write_text("Stage: prediction\nPipeline completed. Results: /run/results\n")
            update = dashboard.completed_pipeline_log_update({"logFile": str(log)})
            self.assertEqual(update["status"], "completed")
            self.assertEqual(update["exitCode"], 0)
            self.assertEqual(update["slurmState"], "COMPLETED_FROM_LOG")
            log.write_text("Stage: prediction\nStill running\n")
            self.assertIsNone(dashboard.completed_pipeline_log_update({"logFile": str(log)}))

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

    def test_semantic_analysis_prepares_target_reactor_before_dry_run(self) -> None:
        script = (Path(__file__).parents[1] / "scripts" / "run_semantic_analysis.sh").read_text()
        self.assertLess(script.index('"${MAVEN[@]}" -DskipTests install'),
                        script.index('rewrite-maven-plugin:6.12.0:dryRunNoFork'))

    def test_maven_command_falls_back_when_repository_has_no_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            configured = root / "mvn"
            configured.write_text("#!/bin/sh\nexit 0\n")
            configured.chmod(0o755)
            with mock.patch.dict(os.environ, {"DSARP_MAVEN": str(configured)}):
                self.assertEqual(maven_command(repository), [str(configured.resolve())])

    def test_pipeline_short_circuits_expensive_post_validation_stages(self) -> None:
        script = (Path(__file__).parents[1] / "scripts" / "run_log4j2_pipeline.sh").read_text()
        self.assertIn("has_validated_changes()", script)
        self.assertIn("Skipped: no validated source changes require formatting.", script)
        self.assertIn("no candidate passed isolated validation", script)
        self.assertIn('post-validation-skip.json', script)

    def test_pipeline_only_accepts_exact_persistent_log4j_cron_flake(self) -> None:
        script = (Path(__file__).parents[1] / "scripts" / "run_log4j2_pipeline.sh").read_text()
        self.assertIn(
            "RollingAppenderDirectCronTest#testAppender", script)
        self.assertIn(
            "Expecting actual not to be empty within 2 seconds", script)
        self.assertIn(
            "Rollover completion verification failure", script)
        self.assertIn("complete_reactor_after_accepted_test_failure", script)
        self.assertIn('maven_for "$repository" -DskipTests verify', script)

    def test_generic_reactor_builds_all_bytecode_before_candidate_tests(self) -> None:
        script = (Path(__file__).parents[1] / "scripts" / "run_log4j2_pipeline.sh").read_text()
        self.assertIn('if [[ "$PROFILE" != "log4j2" ]]', script)
        self.assertIn('verify_arguments=(-DskipTests verify)', script)
        self.assertIn('[[ -f "$BASE_REPO/pom.xml" ]]', script)
        self.assertIn("currently support Maven Java repositories only", script)

    def test_cumulative_validation_uses_its_own_worktree(self) -> None:
        script = (Path(__file__).parents[1] / "evaluation" /
                  "validate_openrewrite_candidates.py").read_text()
        self.assertNotIn("maven_command(aggregate_worktree)", script)
        self.assertIn('maven_command(cumulative_worktree) + ["-DskipTests", "verify"]', script)
        validator = (Path(__file__).parents[1] / "evaluation" /
                     "validate_openrewrite_candidates.py").read_text()
        self.assertIn('test_command += ["-DforkCount=1", "test"]', validator)
        self.assertIn('["-pl", ",".join(modules), "-am", "-amd"]', validator)

    def test_semantic_analysis_has_dedicated_heap_and_visible_fallback(self) -> None:
        hpc = (Path(__file__).parents[1] / "hpc" / "noctua_pipeline.sbatch").read_text()
        pipeline = (Path(__file__).parents[1] / "scripts" /
                    "run_log4j2_pipeline.sh").read_text()
        self.assertIn("DSARP_SEMANTIC_MAVEN_OPTS", hpc)
        self.assertIn("-Xmx28g", hpc)
        self.assertIn('SEMANTIC_STATUS="$RESULTS_DIR/semantic-analysis/status.json"', pipeline)
        self.assertIn('"fallback_used": True', pipeline)
        self.assertIn('semantic_reason="java_heap_exhausted"', pipeline)

    def test_candidate_evidence_is_archived_for_download(self) -> None:
        validator = (Path(__file__).parents[1] / "evaluation" /
                     "validate_openrewrite_candidates.py").read_text()
        server = (Path(__file__).parents[1] / "webui" / "server.py").read_text()
        self.assertIn('candidate-validation-evidence.zip', validator)
        self.assertIn('results/openrewrite-validation/candidate-validation-evidence.zip', server)

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
            self.assertEqual(submit.call_args.kwargs["env"]["PROFILE"], "log4j2")
            self.assertEqual(submit.call_args.kwargs["env"]["INCLUDE_CURATED_FILESIZE"], "1")
            self.assertEqual(submit.call_args.kwargs["env"]["STOP_STAGE"], "summary")
            self.assertEqual(submit.call_args.kwargs["env"]["MAX_COMMITS_PER_REPO"], "500")
            self.assertEqual(submit.call_args.kwargs["timeout"], 120)

            tika_csvs = []
            for name in ("component-metrics.csv", "smell-characteristics.csv", "smell-affects.csv"):
                content = "project,versionId\ntika,697d7c0\n"
                tika_csvs.append({
                    "name": name,
                    "data": __import__("base64").b64encode(content.encode()).decode(),
                })
            with (mock.patch.object(dashboard, "RUNS", runs),
                  mock.patch.object(dashboard, "STATE", root / "state"),
                  mock.patch.object(dashboard, "HPC_PROJECT_SPACE", root / "scratch"),
                  mock.patch.object(dashboard, "HPC_RUNS_ROOT", root / "scratch" / "runs"),
                  mock.patch.object(dashboard, "EXECUTION_MODE", "hpc"),
                  mock.patch.object(dashboard, "hpc_available", return_value=True),
                  mock.patch.object(dashboard, "latest_compatible_predictions", return_value=predictions),
                  mock.patch.object(dashboard.subprocess, "run", return_value=completed) as submit):
                tika = dashboard.start_hpc_run({
                    "runName": "Tika generic check", "system": "tika",
                    "repositoryUrl": "https://github.com/apache/tika.git",
                    "versionId": "697d7c0", "mode": "latest_predictions",
                    "includeCuratedFileSize": True,  # stale clients must be ignored
                    "baselineFiles": tika_csvs, "severityCategories": ["high"],
                    "batchSize": 10, "startBatch": 1, "maxBatches": 1,
                })
            self.assertEqual(tika["compatibilityProfile"], "none")
            self.assertEqual(submit.call_args.kwargs["env"]["PROFILE"], "generic")
            self.assertEqual(submit.call_args.kwargs["env"]["INCLUDE_CURATED_FILESIZE"], "0")


if __name__ == "__main__":
    unittest.main()
