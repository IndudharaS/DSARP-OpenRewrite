#!/usr/bin/env python3
"""Validate generated OpenRewrite candidates in isolated Git worktrees."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
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
    args = parser.parse_args()

    repository = args.repository.resolve()
    generated = args.generated_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((generated / "manifest.json").read_text(encoding="utf-8"))
    candidates = [row for row in manifest["records"] if row["status"] == "ready_for_dry_run"]
    results, validated = [], []
    worktrees = output / "worktrees"
    worktrees.mkdir(exist_ok=True)

    # OpenRewrite's Maven goal is an aggregator. In some multi-module builds it
    # resolves reactor SNAPSHOT dependencies before the lifecycle goals that
    # precede it on the same command line. Install the unchanged reactor once so
    # candidate failures reflect the recipe itself, not missing local artifacts.
    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(args.java_home.resolve())
    prepare_log = output / "dependency-preparation.log"
    if args.skip_dependency_preparation:
        prepare_log.write_text("Dependency preparation explicitly skipped.\n", encoding="utf-8")
        prepare_status = 0
    else:
        prepare_status = run(
            ["./mvnw", "-DskipTests", "install"],
            cwd=repository,
            log=prepare_log,
            env=environment,
        )
    if prepare_status != 0:
        raise SystemExit(
            f"Could not install reactor dependencies before OpenRewrite validation; "
            f"see {prepare_log}"
        )

    for record in candidates:
        prediction = int(record["prediction_id"])
        worktree = worktrees / f"prediction-{prediction:04d}"
        recipe = generated / str(record["recipe_file"])
        log_dir = output / "logs" / f"prediction-{prediction:04d}"
        if (not args.allow_risky_candidates
                and record.get("risk_level") == "high_public_api" and not has_compatibility_strategy(
            record, args.compatibility_profile
        )):
            results.append({
                **record,
                "validation_status": "manual_review",
                "failure_category": "missing_compatibility_strategy",
                "validation_reason": (
                    "Public API move was not executed automatically; provide a project-specific "
                    "binary/source compatibility strategy first"
                ),
                "diagnostic_log": "",
            })
            continue
        if worktree.exists():
            raise SystemExit(f"Validation worktree already exists: {worktree}")
        added = run(["git", "-C", str(repository), "worktree", "add", "--detach",
                     str(worktree), "HEAD"], log=log_dir / "worktree.log")
        status, reason, category = "failed", "could not create validation worktree", "worktree"
        diagnostic_log = log_dir / "worktree.log"
        changed_files: list[str] = []
        if added == 0:
            rewrite = run([
                str(args.rewrite_runner.resolve()), "--repository", str(worktree),
                "--java-home", str(args.java_home.resolve()), "--recipe", str(recipe),
                "--active-recipe", str(record["recipe_name"]),
                "--results-dir", str(log_dir / "rewrite-results"),
                "--log-dir", str(log_dir), "--mode", "apply",
            ], log=log_dir / "runner-console.log")
            compatibility_status = 0
            format_status = 1
            verify_status = 1
            changed_files = source_changes(worktree) if rewrite == 0 else []
            changed = bool(changed_files)
            if changed:

                # FileSize is a published Log4j2 API. Its move is valid only with the
                # compatibility facade supplied by the experiment runner.
                if (args.compatibility_profile == "log4j2" and
                        record["source_type"] == "org.apache.logging.log4j.core.appender.rolling.FileSize"):
                    compatibility_status = run([
                        str(args.rewrite_runner.resolve()), "--repository", str(worktree),
                        "--java-home", str(args.java_home.resolve()), "--recipe", str(recipe),
                        "--active-recipe", str(record["recipe_name"]),
                        "--results-dir", str(log_dir / "rewrite-results"),
                        "--log-dir", str(log_dir), "--mode", "compatibility",
                    ], log=log_dir / "compatibility-console.log")

                if compatibility_status == 0:
                    if uses_spotless(worktree):
                        format_status = run(
                            ["./mvnw", "-DskipTests", "spotless:apply"],
                            cwd=worktree, log=log_dir / "spotless.log", env=environment,
                        )
                    else:
                        (log_dir / "spotless.log").write_text(
                            "Spotless is not configured; formatting was skipped.\n", encoding="utf-8"
                        )
                        format_status = 0
                if format_status == 0:
                    verify_status = run(
                        ["./mvnw", "-DskipTests", "verify"],
                        cwd=worktree, log=log_dir / "verify.log", env=environment,
                    )

            if rewrite == 0 and changed and compatibility_status == 0 and format_status == 0 and verify_status == 0:
                status, reason = "validated", "OpenRewrite application, formatting, and Maven verification passed"
                category = "validated"
                diagnostic_log = log_dir / "verify.log"
                validated.append(record)
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
            run(["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)])
        results.append({
            **record,
            "validation_status": status,
            "failure_category": category,
            "validation_reason": reason,
            "diagnostic_log": str(diagnostic_log.relative_to(output)),
            "changed_files": changed_files,
        })

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
        fields = ["prediction_id", "source_type", "destination_type", "model_rank", "model_score",
                  "candidate_score", "risk_level", "validation_status", "failure_category",
                  "validation_reason", "diagnostic_log", "changed_files"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(results)
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, indent=2))
    if not validated:
        print("No generated candidate passed isolated Maven verification; no recipe will be applied.")


if __name__ == "__main__":
    main()
