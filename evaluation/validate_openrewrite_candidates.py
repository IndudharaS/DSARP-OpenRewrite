#!/usr/bin/env python3
"""Validate generated OpenRewrite candidates in isolated Git worktrees."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    log: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    process = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=env or os.environ.copy())
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(process.stdout, encoding="utf-8")
    return process.returncode


def aggregate(records: list[dict[str, object]]) -> str:
    lines = [
        "---", "type: specs.openrewrite.org/v1beta/recipe",
        "name: generated.architecture.ApplyValidatedCandidates",
        "displayName: Apply fully validated architecture candidates",
        "description: Applies generated candidates that passed isolated Maven verification.",
        "recipeList:",
    ]
    for record in records:
        lines += [
            "  - org.openrewrite.java.ChangeType:",
            f"      oldFullyQualifiedTypeName: {record['source_type']}",
            f"      newFullyQualifiedTypeName: {record['destination_type']}",
        ]
    return "\n".join(lines) + "\n"


def uses_spotless(repository: Path) -> bool:
    for pom in repository.rglob("pom.xml"):
        try:
            if "spotless-maven-plugin" in pom.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def source_changes(repository: Path) -> list[str]:
    """Return relevant tracked/untracked source and build files changed by a recipe."""
    process = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if process.returncode != 0:
        return []
    relevant_suffixes = (".java", ".kt", ".groovy", ".scala", ".xml", ".gradle", ".kts")
    changed = []
    for line in process.stdout.splitlines():
        value = line[3:].strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value.endswith(relevant_suffixes) or Path(value).name in {"pom.xml", "module-info.java"}:
            changed.append(value)
    return sorted(set(changed))


def affected_maven_projects(repository: Path, changed_files: list[str]) -> list[str]:
    """Return the nearest Maven modules containing changed files."""
    projects: set[str] = set()
    for changed in changed_files:
        path = (repository / changed).parent
        while path != repository and repository in path.parents:
            if (path / "pom.xml").is_file():
                projects.add(str(path.relative_to(repository)))
                break
            path = path.parent
    return sorted(projects)


def classify_failure(log: Path, fallback: str) -> tuple[str, str]:
    """Turn Maven/OpenRewrite output into a useful, stable failure category."""
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    lowered = text.lower()
    rules = (
        ("compilation", ("compilation failure", "cannot find symbol", "package does not exist", "incompatible types")),
        ("api_compatibility", ("baseline version", "binary incompatible", "baseline problems detected")),
        ("module_metadata", ("bnd-maven-plugin", "split package", "export-package", "import-package")),
        ("dependency_resolution", ("could not resolve dependencies", "could not find artifact", "failed to collect dependencies")),
        ("test_failure", ("there are test failures", "tests run:", "surefire")),
        ("recipe_execution", ("recipe run failed", "rewrite:run", "recipe not found")),
    )
    for category, needles in rules:
        if any(needle in lowered for needle in needles):
            return category, f"{fallback} ({category.replace('_', ' ')})"
    return "tooling_or_build", fallback


def has_compatibility_strategy(record: dict[str, object], profile: str) -> bool:
    return (
        profile == "log4j2"
        and record.get("source_type")
        == "org.apache.logging.log4j.core.appender.rolling.FileSize"
    )


WORKTREE_LOCK = threading.Lock()


def validate_candidate(
    record: dict[str, object], *, repository: Path, generated: Path, output: Path,
    worktrees: Path, rewrite_runner: Path, java_home: Path, environment: dict[str, str],
    compatibility_profile: str, allow_risky_candidates: bool, batch_number: int,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Validate one candidate in an isolated worktree and return its evidence."""
    prediction = int(record["prediction_id"])
    worktree = worktrees / f"prediction-{prediction:04d}"
    recipe = generated / str(record["recipe_file"])
    log_dir = output / "logs" / f"prediction-{prediction:04d}"
    if (not allow_risky_candidates and record.get("risk_level") == "high_public_api"
            and not has_compatibility_strategy(record, compatibility_profile)):
        return ({
            **record, "batch_number": batch_number, "validation_status": "manual_review",
            "failure_category": "missing_compatibility_strategy",
            "validation_reason": (
                "Public API move was not executed automatically; provide a project-specific "
                "binary/source compatibility strategy first"
            ), "diagnostic_log": "", "changed_files": [],
        }, None)
    if worktree.exists():
        return ({
            **record, "batch_number": batch_number, "validation_status": "failed",
            "failure_category": "worktree", "validation_reason": "validation worktree already exists",
            "diagnostic_log": "", "changed_files": [],
        }, None)
    # Git serializes worktree metadata updates. The expensive rewrite and Maven
    # work remains parallel after this short critical section.
    with WORKTREE_LOCK:
        added = run(["git", "-C", str(repository), "worktree", "add", "--detach",
                     str(worktree), "HEAD"], log=log_dir / "worktree.log")
    status, reason, category = "failed", "could not create validation worktree", "worktree"
    diagnostic_log = log_dir / "worktree.log"
    changed_files: list[str] = []
    validation_scope = "none"
    try:
        if added == 0:
            rewrite = run([
                str(rewrite_runner.resolve()), "--repository", str(worktree),
                "--java-home", str(java_home.resolve()), "--recipe", str(recipe),
                "--active-recipe", str(record["recipe_name"]),
                "--results-dir", str(log_dir / "rewrite-results"),
                "--log-dir", str(log_dir), "--mode", "apply",
            ], log=log_dir / "runner-console.log", env=environment)
            compatibility_status, format_status, verify_status = 0, 1, 1
            changed_files = source_changes(worktree) if rewrite == 0 else []
            changed = bool(changed_files)
            if changed:
                if (compatibility_profile == "log4j2" and record["source_type"] ==
                        "org.apache.logging.log4j.core.appender.rolling.FileSize"):
                    compatibility_status = run([
                        str(rewrite_runner.resolve()), "--repository", str(worktree),
                        "--java-home", str(java_home.resolve()), "--recipe", str(recipe),
                        "--active-recipe", str(record["recipe_name"]),
                        "--results-dir", str(log_dir / "rewrite-results"),
                        "--log-dir", str(log_dir), "--mode", "compatibility",
                    ], log=log_dir / "compatibility-console.log", env=environment)
                if compatibility_status == 0:
                    if uses_spotless(worktree):
                        format_status = run(["./mvnw", "-DskipTests", "spotless:apply"],
                                            cwd=worktree, log=log_dir / "spotless.log", env=environment)
                    else:
                        (log_dir / "spotless.log").write_text(
                            "Spotless is not configured; formatting was skipped.\n", encoding="utf-8")
                        format_status = 0
                if format_status == 0:
                    maven_threads = environment.get("DSARP_MAVEN_THREADS", "").strip()
                    parallel = ["-T", maven_threads] if maven_threads else []
                    projects = affected_maven_projects(worktree, changed_files)
                    project_args = ["-pl", ",".join(projects), "-am"] if projects else []
                    validation_scope = "affected_modules:" + ",".join(projects) if projects else "full_reactor"
                    verify_status = run(["./mvnw", *parallel, *project_args, "-DskipTests", "verify"], cwd=worktree,
                                        log=log_dir / "verify.log", env=environment)
            if rewrite == 0 and changed and compatibility_status == 0 and format_status == 0 and verify_status == 0:
                status, reason, category = "validated", "OpenRewrite application, formatting, and affected-module Maven verification passed", "validated"
                diagnostic_log = log_dir / "verify.log"
            elif rewrite != 0:
                diagnostic_log = log_dir / "runner-console.log"
                category, reason = classify_failure(diagnostic_log, "OpenRewrite application failed")
            elif not changed:
                status, reason, category = "not_applicable", "recipe made no source changes at the selected commit", "no_source_change"
                diagnostic_log = log_dir / "runner-console.log"
            elif compatibility_status != 0:
                diagnostic_log = log_dir / "compatibility-console.log"
                category, reason = classify_failure(diagnostic_log, "public-API compatibility installation failed")
            elif format_status != 0:
                diagnostic_log = log_dir / "spotless.log"
                category, reason = classify_failure(diagnostic_log, "post-rewrite formatting failed")
            else:
                diagnostic_log = log_dir / "verify.log"
                category, reason = classify_failure(diagnostic_log, "post-rewrite Maven verification failed")
    finally:
        if worktree.exists():
            with WORKTREE_LOCK:
                run(["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)])
    result = {
        **record, "batch_number": batch_number, "validation_status": status,
        "failure_category": category, "validation_reason": reason,
        "diagnostic_log": str(diagnostic_log.relative_to(output)), "changed_files": changed_files,
        "validation_scope": validation_scope,
    }
    return result, record if status == "validated" else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--generated-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--rewrite-runner", required=True, type=Path)
    parser.add_argument("--java-home", required=True, type=Path)
    parser.add_argument("--compatibility-profile", choices=("none", "log4j2"), default="none")
    parser.add_argument(
        "--allow-risky-candidates", action="store_true",
        help=("execute high-risk public-API candidates in isolated worktrees; "
              "they must still produce a source change and pass Maven verification"),
    )
    parser.add_argument(
        "--skip-dependency-preparation", action="store_true",
        help="skip the initial reactor install only when dependencies were already prepared",
    )
    parser.add_argument("--batch-size", type=int, default=10,
                        help="candidates per validation batch (default: 10)")
    parser.add_argument("--start-batch", type=int, default=1,
                        help="one-based first batch to execute (default: 1)")
    parser.add_argument("--max-batches", type=int, default=0,
                        help="maximum batches to execute; 0 processes every batch")
    parser.add_argument("--parallel-workers", type=int, default=1,
                        help="isolated candidates to validate concurrently (default: 1)")
    args = parser.parse_args()
    if args.batch_size < 1 or args.start_batch < 1 or args.max_batches < 0 or args.parallel_workers < 1:
        parser.error("batch size/start must be positive and --max-batches cannot be negative")

    repository = args.repository.resolve()
    generated = args.generated_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((generated / "manifest.json").read_text(encoding="utf-8"))
    severity_order = {"high": 0, "medium": 1, "low": 2}
    candidates = sorted(
        (row for row in manifest["records"] if row["status"] == "ready_for_dry_run"),
        key=lambda row: (severity_order.get(str(row.get("severity")), 3),
                         -int(row.get("severity_score") or 0), int(row["prediction_id"])),
    )
    results, validated = [], []
    configured_worktree_root = os.environ.get("DSARP_VALIDATION_WORKTREE_ROOT", "").strip()
    worktrees = Path(configured_worktree_root).resolve() if configured_worktree_root else output / "worktrees"
    worktrees.mkdir(exist_ok=True)
    run(["git", "-C", str(repository), "worktree", "prune"])

    # OpenRewrite's Maven goal is an aggregator. In some multi-module builds it
    # resolves reactor SNAPSHOT dependencies before the lifecycle goals that
    # precede it on the same command line. Install the unchanged reactor once so
    # candidate failures reflect the recipe itself, not missing local artifacts.
    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(args.java_home.resolve())
    maven_threads = os.environ.get("DSARP_MAVEN_THREADS", "").strip()
    maven_parallel = ["-T", maven_threads] if maven_threads else []
    prepare_log = output / "dependency-preparation.log"
    if args.skip_dependency_preparation:
        prepare_log.write_text("Dependency preparation explicitly skipped.\n", encoding="utf-8")
        prepare_status = 0
    else:
        prepare_status = run(
            ["./mvnw", *maven_parallel, "-DskipTests", "install"],
            cwd=repository,
            log=prepare_log,
            env=environment,
        )
    if prepare_status != 0:
        raise SystemExit(
            f"Could not install reactor dependencies before OpenRewrite validation; "
            f"see {prepare_log}"
        )

    start_index = (args.start_batch - 1) * args.batch_size
    end_index = start_index + args.batch_size * args.max_batches if args.max_batches else len(candidates)
    executed_candidates = candidates[start_index:end_index]
    deferred_candidates = candidates[:start_index] + candidates[end_index:]
    selection_report = {
        "generated_candidate_count": len(candidates),
        "selected_candidate_count": len(executed_candidates),
        "deferred_candidate_count": len(deferred_candidates),
        "batch_size": args.batch_size,
        "start_batch": args.start_batch,
        "configured_max_batches": args.max_batches,
        "parallel_workers": args.parallel_workers,
        "records": [
            {**record, "batch_number": position // args.batch_size + 1,
             "selection_status": "selected_for_validation"}
            for position, record in enumerate(candidates)
            if start_index <= position < end_index
        ],
    }
    (output / "selection-report.json").write_text(
        json.dumps(selection_report, indent=2) + "\n", encoding="utf-8")

    print(f"Executing {len(executed_candidates)} of {len(candidates)} generated candidates "
          f"with {args.parallel_workers} parallel worker(s).", flush=True)
    with ThreadPoolExecutor(max_workers=args.parallel_workers) as executor:
        futures = {}
        for ordinal, (position, record) in enumerate(
                zip(range(start_index, start_index + len(executed_candidates)), executed_candidates), start=1):
            batch_number = position // args.batch_size + 1
            print(f"Queued validation candidate {ordinal}/{len(executed_candidates)} "
                  f"(prediction {record['prediction_id']}, batch {batch_number}, severity {record.get('severity')})",
                  flush=True)
            future = executor.submit(
                validate_candidate, record, repository=repository, generated=generated, output=output,
                worktrees=worktrees, rewrite_runner=args.rewrite_runner, java_home=args.java_home,
                environment=environment, compatibility_profile=args.compatibility_profile,
                allow_risky_candidates=args.allow_risky_candidates, batch_number=batch_number,
            )
            futures[future] = (ordinal, record)
        completed = 0
        for future in as_completed(futures):
            ordinal, record = futures[future]
            try:
                result, accepted = future.result()
            except Exception as error:  # Preserve the remaining batch and its evidence.
                result, accepted = ({
                    **record,
                    "batch_number": (start_index + ordinal - 1) // args.batch_size + 1,
                    "validation_status": "failed",
                    "failure_category": "validator_internal_error",
                    "validation_reason": f"candidate validator raised {type(error).__name__}: {error}",
                    "diagnostic_log": "",
                    "changed_files": [],
                    "validation_scope": "none",
                }, None)
            results.append(result)
            if accepted is not None:
                validated.append(accepted)
            completed += 1
            print(f"Completed validation {completed}/{len(executed_candidates)}: prediction "
                  f"{record['prediction_id']} -> {result['validation_status']}", flush=True)

    results.sort(key=lambda row: int(row["prediction_id"]))
    validated.sort(key=lambda row: int(row["prediction_id"]))

    executed_ids = {int(record["prediction_id"]) for record in executed_candidates}
    for position, record in enumerate(candidates):
        if int(record["prediction_id"]) in executed_ids:
            continue
        results.append({
            **record,
            "batch_number": position // args.batch_size + 1,
            "validation_status": "deferred_batch_limit",
            "failure_category": "batch_limit",
            "validation_reason": "candidate was deferred by the configured maximum batch count",
            "diagnostic_log": "",
            "changed_files": [],
        })

    results.sort(key=lambda row: int(row["prediction_id"]))

    (output / "validated-candidates.yml").write_text(aggregate(validated), encoding="utf-8")
    status_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for record in results:
        status = str(record["validation_status"])
        category = str(record["failure_category"])
        status_counts[status] = status_counts.get(status, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
    report = {
        "candidate_count": len(candidates),
        "selected_candidate_count": len(executed_candidates),
        "executed_candidate_count": (
            status_counts.get("validated", 0) + status_counts.get("failed", 0)
            + status_counts.get("not_applicable", 0)
        ),
        "deferred_candidate_count": len(deferred_candidates),
        "batch_size": args.batch_size,
        "start_batch": args.start_batch,
        "configured_max_batches": args.max_batches,
        "total_batches": (len(candidates) + args.batch_size - 1) // args.batch_size,
        "executed_batches": (len(executed_candidates) + args.batch_size - 1) // args.batch_size,
        "parallel_workers": args.parallel_workers,
        "validated_count": status_counts.get("validated", 0),
        "failed_count": status_counts.get("failed", 0),
        "not_applicable_count": status_counts.get("not_applicable", 0),
        "manual_review_count": status_counts.get("manual_review", 0),
        "status_counts": status_counts,
        "failure_category_counts": category_counts,
        "records": results,
    }
    (output / "validation-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with (output / "validation-report.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["prediction_id", "severity", "severity_score", "batch_number",
                  "source_type", "destination_type", "model_rank", "model_score",
                  "candidate_score", "risk_level", "validation_status", "failure_category",
                  "validation_reason", "diagnostic_log", "changed_files"]
        fields.append("validation_scope")
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(results)
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, indent=2))
    if not validated:
        print("No generated candidate passed isolated Maven verification; no recipe will be applied.")


if __name__ == "__main__":
    main()
