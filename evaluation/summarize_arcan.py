#!/usr/bin/env python3
"""Summarize and compare raw CSV output produced by Arcan 1.2.1."""

from __future__ import annotations

import argparse
import ast
import csv
import json
from pathlib import Path


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def cycles(path: Path) -> list[list[str]]:
    result = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        members = header[1:]
        for row in reader:
            result.append(sorted(name for name, flag in zip(members, row[1:]) if flag == "1"))
    return result


def summarize(directory: Path, compiled_classes: int | None = None) -> dict[str, object]:
    package_cycles = cycles(directory / "packageCyclicDependencyTable.csv")
    class_cycles = cycles(directory / "classCyclicDependencyTable.csv")
    return {
        "tool": "Arcan",
        "version": "1.2.1",
        "analysis_configuration": "compiled-target-classes/class/all",
        "compiled_classes": compiled_classes,
        "hub_like_dependencies": len(rows(directory / "HL.csv")),
        "unstable_dependencies": len(rows(directory / "UD.csv")),
        "unstable_dependencies_filtered_30": len(rows(directory / "UD30.csv")),
        "package_cycles": len(package_cycles),
        "class_cycles": len(class_cycles),
        "package_metrics_records": len(rows(directory / "PM.csv")),
        "class_metrics_records": len(rows(directory / "CM.csv")),
        "package_cycle_members": package_cycles,
        "class_cycle_members": class_cycles,
    }


def summarize_baseline(directory: Path) -> dict[str, object]:
    required = {
        "component_metrics": directory / "component-metrics.csv",
        "smell_characteristics": directory / "smell-characteristics.csv",
        "smell_affects": directory / "smell-affects.csv",
    }
    missing = [path.name for path in required.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing baseline CSV file(s): {', '.join(missing)}")

    metrics = rows(required["component_metrics"])
    smells = rows(required["smell_characteristics"])
    # Read this file as part of validation even though the summary uses the
    # canonical AffectedElements field from smell-characteristics.csv.
    affects = rows(required["smell_affects"])
    schemas = {
        "component-metrics.csv": (metrics, {"project", "versionId", "ComponentType", "name"}),
        "smell-characteristics.csv": (
            smells,
            {"project", "versionId", "smellType", "AffectedConstructType", "AffectedElements"},
        ),
        "smell-affects.csv": (affects, {"project", "versionId", "from", "to"}),
    }
    for filename, (records, expected) in schemas.items():
        actual = set(records[0]) if records else set()
        missing_columns = sorted(expected - actual)
        if missing_columns:
            raise SystemExit(f"{filename} is missing column(s): {', '.join(missing_columns)}")
    projects = sorted({row.get("project", "") for row in smells if row.get("project")})
    versions = sorted({row.get("versionId", "") for row in smells if row.get("versionId")})
    package_cycles = []
    class_cycles = []
    for row in smells:
        if row.get("smellType") != "cyclicDep":
            continue
        value = row.get("AffectedElements", "")
        try:
            members = sorted(str(item) for item in ast.literal_eval(value))
        except (SyntaxError, ValueError):
            members = sorted(item.strip() for item in value.strip("[]").split(",") if item.strip())
        if row.get("AffectedConstructType") == "PACKAGE":
            package_cycles.append(members)
        else:
            class_cycles.append(members)

    count = lambda smell_type: sum(row.get("smellType") == smell_type for row in smells)
    return {
        "tool": "Arcan",
        "version": "from supplied CSV metadata",
        "source_format": "arcan-three-csv-export",
        "projects": projects,
        "versions": versions,
        "component_metrics_records": len(metrics),
        "smell_characteristics_records": len(smells),
        "smell_affects_records": len(affects),
        "compiled_classes": None,
        "hub_like_dependencies": count("hubLikeDep"),
        "unstable_dependencies": count("unstableDep"),
        "unstable_dependencies_filtered_30": None,
        "god_components": count("godComponent"),
        "package_cycles": len(package_cycles),
        "class_cycles": len(class_cycles),
        "package_metrics_records": sum(row.get("ComponentType") == "PACKAGE" for row in metrics),
        "class_metrics_records": sum(row.get("ComponentType") != "PACKAGE" for row in metrics),
        "package_cycle_members": package_cycles,
        "class_cycle_members": class_cycles,
    }


def comparison(before: dict[str, object], after: dict[str, object]) -> dict[str, object]:
    metrics = (
        "compiled_classes", "hub_like_dependencies", "unstable_dependencies",
        "unstable_dependencies_filtered_30", "package_cycles", "class_cycles",
        "package_metrics_records", "class_metrics_records",
    )
    compared = {}
    for metric in metrics:
        old, new = before.get(metric), after.get(metric)
        delta = new - old if isinstance(old, int) and isinstance(new, int) else None
        compared[metric] = {"before": old, "after": new, "delta": delta}
    before_packages = {tuple(cycle) for cycle in before["package_cycle_members"]}
    after_packages = {tuple(cycle) for cycle in after["package_cycle_members"]}
    compared["package_cycle_sets"] = {
        "resolved": [list(value) for value in sorted(before_packages - after_packages)],
        "introduced": [list(value) for value in sorted(after_packages - before_packages)],
        "unchanged": [list(value) for value in sorted(before_packages & after_packages)],
    }
    same_version = before.get("version") == after.get("version")
    same_configuration = (
        before.get("analysis_configuration") is not None
        and before.get("analysis_configuration") == after.get("analysis_configuration")
    )
    comparable = same_version and same_configuration
    return {
        "tool": "Arcan",
        "baseline_version": before.get("version"),
        "refactored_version": after.get("version"),
        "baseline_configuration": before.get("analysis_configuration"),
        "refactored_configuration": after.get("analysis_configuration"),
        "aggregate_counts_comparable": comparable,
        "comparison_warning": None if comparable else (
            "Baseline and refactored reports do not prove identical Arcan version and analysis configuration; "
            "aggregate deltas are not valid causal evidence."
        ),
        "metrics": compared,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    one = subparsers.add_parser("summarize")
    one.add_argument("raw_directory", type=Path)
    one.add_argument("--output", required=True, type=Path)
    one.add_argument("--compiled-classes", type=int)
    baseline = subparsers.add_parser("baseline-csv")
    baseline.add_argument("csv_directory", type=Path)
    baseline.add_argument("--output", required=True, type=Path)
    two = subparsers.add_parser("compare")
    two.add_argument("before", type=Path)
    two.add_argument("after", type=Path)
    two.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.command == "summarize":
        result = summarize(args.raw_directory, args.compiled_classes)
    elif args.command == "baseline-csv":
        result = summarize_baseline(args.csv_directory)
    else:
        result = comparison(
            json.loads(args.before.read_text(encoding="utf-8")),
            json.loads(args.after.read_text(encoding="utf-8")),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    if args.command == "compare":
        csv_output = args.output.with_suffix(".csv")
        with csv_output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["metric", "before", "after", "delta"])
            writer.writeheader()
            for metric, values in result["metrics"].items():
                if metric == "package_cycle_sets":
                    continue
                writer.writerow({"metric": metric, **values})
    print(rendered, end="")


if __name__ == "__main__":
    main()
