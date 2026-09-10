#!/usr/bin/env python3
"""Convert architecture-smell predictions into reviewable OpenRewrite recipes.

The generator is repository-independent. It discovers Java types and import
dependencies, then concretizes generic ``Move Class`` recommendations by
ranking direct dependency candidates between affected packages. Repeated smell
rows diversify source types instead of discarding every equally ranked tie.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from openrewrite.resolvers.move_method import resolve_move_method
from openrewrite.resolvers.move_class import rank_semantic_move_classes
from openrewrite.semantic_analysis import load_methods


PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;", re.MULTILINE)
IMPORT_RE = re.compile(
    r"^\s*import\s+(?:static\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)(?:\.\*)?\s*;",
    re.MULTILINE,
)
TYPE_RE = re.compile(
    r"\b(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|sealed\s+|non-sealed\s+)*"
    r"(?:class|interface|enum|record|@interface)\s+([A-Za-z_$][\w$]*)"
)
RANKED_SUGGESTION_RE = re.compile(r"^\s*(.*?)\s*(?:\(([0-9]*\.?[0-9]+)\))?\s*$")
EXCLUDED_PARTS = {"target", "build", ".gradle", ".git", "generated", "node_modules"}


@dataclass(frozen=True)
class JavaType:
    qualified_name: str
    package: str
    simple_name: str
    path: str
    imports: tuple[str, ...]
    module: str
    source_set: str
    is_public: bool


@dataclass
class ManifestRecord:
    prediction_id: int
    architecture_smell: str
    affected_elements: list[str]
    predicted_refactoring: str
    top_refactoring: str | None
    model_rank: int | None
    model_score: float | None
    source_type: str | None
    destination_type: str | None
    source_package: str | None
    destination_package: str | None
    status: str
    reason: str
    recipe_name: str | None
    recipe_file: str | None
    candidate_score: float | None
    risk_level: str | None
    severity: str
    severity_score: int
    severity_reason: str
    refactoring_kind: str | None = None
    analysis_source: str | None = None
    source_member: str | None = None
    source_signature: str | None = None
    destination_member: str | None = None
    destination_class: str | None = None
    foreign_affinity: float | None = None
    destination_class_affinity: float | None = None
    source_state_penalty: float | None = None
    structural_score: float | None = None
    precondition_status: str | None = None


def classify_severity(smell: str, affected_count: int) -> tuple[str, int, str]:
    """Prioritize smells using transparent architecture-level evidence."""
    normalized = smell.lower()
    if "cyclic" in normalized or "cycle" in normalized:
        smell_weight, smell_reason = 3, "cyclic dependency"
    elif "hub" in normalized:
        smell_weight, smell_reason = 3, "hub-like dependency"
    elif "unstable" in normalized:
        smell_weight, smell_reason = 2, "unstable dependency"
    else:
        smell_weight, smell_reason = 1, "other smell type"
    scope_weight = 2 if affected_count >= 8 else 1 if affected_count >= 4 else 0
    score = smell_weight + scope_weight
    severity = "high" if score >= 4 else "medium" if score >= 2 else "low"
    return severity, score, f"{smell_reason}; {affected_count} affected elements"


def java_files(repository: Path) -> Iterable[Path]:
    for path in repository.rglob("*.java"):
        if not any(part in EXCLUDED_PARTS for part in path.parts):
            yield path


def parse_repository(repository: Path) -> tuple[dict[str, JavaType], dict[str, set[str]]]:
    types: dict[str, JavaType] = {}
    packages: dict[str, set[str]] = defaultdict(set)

    for path in java_files(repository):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        package_match = PACKAGE_RE.search(text)
        type_match = TYPE_RE.search(text)
        if not package_match or not type_match:
            continue
        package = package_match.group(1)
        simple_name = type_match.group(1)
        qualified_name = f"{package}.{simple_name}"
        imports = tuple(dict.fromkeys(IMPORT_RE.findall(text)))
        relative = path.relative_to(repository)
        parts = relative.parts
        src_index = parts.index("src") if "src" in parts else -1
        module = str(Path(*parts[:src_index])) if src_index > 0 else "."
        source_set = parts[src_index + 1] if src_index >= 0 and len(parts) > src_index + 1 else "unknown"
        java_type = JavaType(
            qualified_name=qualified_name,
            package=package,
            simple_name=simple_name,
            path=str(relative),
            imports=imports,
            module=module,
            source_set=source_set,
            is_public=bool(re.search(r"\bpublic\b", type_match.group(0))),
        )
        types[qualified_name] = java_type
        packages[package].add(qualified_name)

    return types, packages


def imported_type(import_name: str, types: dict[str, JavaType]) -> JavaType | None:
    current = import_name
    while "." in current:
        if current in types:
            return types[current]
        current = current.rsplit(".", 1)[0]
    return None


def dependency_candidates(
    affected: set[str], types: dict[str, JavaType]
) -> dict[tuple[str, str], Counter[str]]:
    """Return imported types keyed by importer-package -> imported-package."""
    edges: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for owner in types.values():
        if owner.package not in affected:
            continue
        for import_name in owner.imports:
            target = imported_type(import_name, types)
            if not target or target.package == owner.package or target.package not in affected:
                continue
            edges[(owner.package, target.package)][target.qualified_name] += 1
    return edges


def ranked_suggestions(raw: str) -> list[tuple[str, float | None]]:
    if not raw or raw.lower() == "nan":
        return []
    ranked: list[tuple[str, float | None]] = []
    for value in raw.split("|"):
        match = RANKED_SUGGESTION_RE.match(value)
        if not match or not match.group(1).strip():
            continue
        ranked.append(
            (match.group(1).strip(), float(match.group(2)) if match.group(2) else None)
        )
    return ranked


def safe_fragment(value: str) -> str:
    fragment = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return fragment or "Candidate"


def is_test_namespace(package: str) -> bool:
    return any(part in {"test", "tests", "testing"} for part in package.split("."))


def rank_move_classes(
    affected: set[str], types: dict[str, JavaType]
) -> tuple[list[tuple[JavaType, str, float, str]], str]:
    edges = dependency_candidates(affected, types)
    reciprocal_pairs: list[tuple[str, str]] = []
    for source, target in edges:
        if (target, source) in edges and source < target:
            reciprocal_pairs.append((source, target))

    if not reciprocal_pairs:
        return [], "no direct reciprocal package dependency found"

    candidates: list[tuple[int, int, int, int, int, str, str, JavaType]] = []
    for left, right in reciprocal_pairs:
        left_to_right = edges[(left, right)]
        right_to_left = edges[(right, left)]
        directions = [
            (left, right, left_to_right),
            (right, left, right_to_left),
        ]
        for importer_package, imported_package, imported_types in directions:
            for qualified_name, import_count in imported_types.items():
                candidate = types[qualified_name]
                # Prefer the weaker direction and a type referenced by fewer files.
                direction_weight = sum(imported_types.values())
                destination_modules = {
                    item.module for item in types.values() if item.package == importer_package
                }
                cross_module = int(candidate.module not in destination_modules)
                production_to_test = int(
                    candidate.source_set == "main" and is_test_namespace(importer_package)
                )
                candidates.append(
                    (
                        production_to_test,
                        cross_module,
                        int(candidate.is_public),
                        direction_weight,
                        import_count,
                        qualified_name,
                        importer_package,
                        candidate,
                    )
                )

    if not candidates:
        return [], "reciprocal packages found but no imported source type was resolved"

    candidates.sort(key=lambda item: item[:-1])
    ranked: list[tuple[JavaType, str, float, str]] = []
    seen: set[tuple[str, str]] = set()
    for production_to_test, cross_module, public_api, direction_weight, import_count, qualified_name, destination, candidate in candidates:
        key = (qualified_name, destination)
        if key in seen or candidate.package == destination:
            continue
        seen.add(key)
        score = round((import_count / max(direction_weight, 1)) * 100.0, 4)
        reason = (
            f"ranked reciprocal dependency candidate; direction weight={direction_weight}, "
            f"type references={import_count}, same module={not bool(cross_module)}, "
            f"public API={bool(public_api)}"
        )
        ranked.append((candidate, destination, score, reason))
    return ranked, "no unused reciprocal dependency candidate remained"


def recipe_yaml(recipe_name: str, source_type: str, destination_type: str, description: str) -> str:
    return f"""---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_name}
displayName: Move {source_type.rsplit('.', 1)[-1]}
description: >-
  {description}
recipeList:
  - org.openrewrite.java.ChangeType:
      oldFullyQualifiedTypeName: {source_type}
      newFullyQualifiedTypeName: {destination_type}
"""


def move_method_recipe_yaml(recipe_name: str, source_class: str, signature: str,
                            target_class: str, description: str) -> str:
    return f"""---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_name}
displayName: Move {signature} to {target_class.rsplit('.', 1)[-1]}
description: >-
  {description}
recipeList:
  - dsarp.rewrite.MoveMethod:
      sourceClass: {source_class}
      methodPattern: {signature}
      targetClass: {target_class}
"""


def aggregate_recipe_yaml(records: list[ManifestRecord]) -> str:
    recipe_name = "generated.architecture.ApplyAllCandidates"
    lines = [
        "---",
        "type: specs.openrewrite.org/v1beta/recipe",
        f"name: {recipe_name}",
        "displayName: Apply all generated architecture candidates",
        "description: Applies every uniquely concretized candidate from the prediction manifest.",
        "recipeList:",
    ]
    for record in records:
        if record.status != "ready_for_dry_run":
            continue
        if record.refactoring_kind == "Move Method":
            lines.extend([
                "  - dsarp.rewrite.MoveMethod:",
                f"      sourceClass: {record.source_type}",
                f"      methodPattern: {record.source_signature}",
                f"      targetClass: {record.destination_class}",
            ])
        else:
            lines.extend([
                "  - org.openrewrite.java.ChangeType:",
                f"      oldFullyQualifiedTypeName: {record.source_type}",
                f"      newFullyQualifiedTypeName: {record.destination_type}",
            ])
    return "\n".join(lines) + "\n"


def generate(args: argparse.Namespace) -> None:
    repository = args.repository.resolve()
    output_dir = args.output_dir.resolve()
    recipe_dir = output_dir / "recipes"
    recipe_dir.mkdir(parents=True, exist_ok=True)

    types, packages = parse_repository(repository)
    semantic_methods = load_methods(getattr(args, "semantic_analysis", None))
    records: list[ManifestRecord] = []
    claimed_sources: dict[str, int] = {}
    claimed_methods: set[tuple[str, str]] = set()
    candidate_count = 0

    with args.predictions.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    required = {args.smell_column, args.elements_column, args.suggestions_column}
    missing = required.difference(rows[0].keys() if rows else set())
    if missing:
        raise SystemExit(f"Prediction CSV is missing columns: {sorted(missing)}")

    for index, row in enumerate(rows, start=1):
        affected = [item.strip() for item in row[args.elements_column].split(args.elements_separator) if item.strip()]
        affected_set = set(affected)
        severity, severity_score, severity_reason = classify_severity(
            row[args.smell_column], len(affected_set)
        )
        selected_severities = set(getattr(args, "severity_categories", "high,medium,low").split(","))
        ranked_predictions = ranked_suggestions(row[args.suggestions_column])
        top_refactoring = ranked_predictions[0][0] if ranked_predictions else None
        first_supported = next(((rank, name, score) for rank, (name, score) in
                                enumerate(ranked_predictions, start=1)
                                if name in {"Move Class", "Move Method"}), None)
        supported = next(
            (
                (rank, name, score)
                for rank, (name, score) in enumerate(ranked_predictions, start=1)
                if name == "Move Class"
            ),
            None,
        )
        suggestion = supported[1] if supported else ""
        record = ManifestRecord(
            prediction_id=index,
            architecture_smell=row[args.smell_column],
            affected_elements=affected,
            predicted_refactoring=suggestion,
            top_refactoring=top_refactoring,
            model_rank=supported[0] if supported else None,
            model_score=supported[2] if supported else None,
            source_type=None,
            destination_type=None,
            source_package=None,
            destination_package=None,
            status="unresolved",
            reason="",
            recipe_name=None,
            recipe_file=None,
            candidate_score=None,
            risk_level=None,
            severity=severity,
            severity_score=severity_score,
            severity_reason=severity_reason,
            refactoring_kind=first_supported[1] if first_supported else None,
        )

        unknown_packages = sorted(affected_set.difference(packages))
        if severity not in selected_severities:
            record.status = "deferred_severity"
            record.reason = f"{severity} severity was not selected for this run"
        elif unknown_packages:
            record.reason = f"affected packages not present at this revision: {', '.join(unknown_packages[:8])}"
        elif not ranked_predictions:
            record.reason = "model produced no ranked recommendation"
        elif not first_supported:
            labels = ", ".join(name for name, _ in ranked_predictions)
            record.status = "unsupported_refactoring"
            record.reason = ("ranked recommendations lack a supported resolver: " + labels +
                             "; Extract Method requires statement/control/data-flow analysis")
        elif len(affected_set) < 2:
            record.reason = f"{first_supported[1]} requires at least two affected packages"
        elif first_supported[1] == "Move Method":
            candidate = resolve_move_method(semantic_methods, affected_set, set(types), claimed_methods)
            record.predicted_refactoring = "Move Method"
            record.model_rank = first_supported[0]
            record.model_score = first_supported[2]
            record.analysis_source = "semantic"
            if candidate is None:
                record.status = "unresolved_method"
                record.reason = "semantic analysis found no method satisfying the conservative static-method subset"
            else:
                source_java_type = types.get(candidate.source_class)
                target_java_type = types.get(candidate.destination_class)
                candidate_status, candidate_reason = candidate.status, candidate.reason
                if source_java_type and target_java_type and source_java_type.module != target_java_type.module:
                    candidate_status, candidate_reason = "cross_module", "source method and destination class are in different Maven modules"
                elif (source_java_type and target_java_type and source_java_type.source_set == "main"
                      and target_java_type.source_set != "main"):
                    candidate_status, candidate_reason = "test_boundary", "production method cannot move into a test source set"
                record.status = candidate_status
                record.reason = candidate_reason
                record.source_type = candidate.source_class
                record.destination_type = candidate.destination_class
                record.source_package = candidate.source_package
                record.destination_package = candidate.destination_package
                record.source_member = candidate.source_member
                record.source_signature = candidate.source_signature
                record.destination_member = candidate.source_member
                record.destination_class = candidate.destination_class
                record.foreign_affinity = candidate.foreign_affinity
                record.destination_class_affinity = candidate.destination_class_affinity
                record.source_state_penalty = candidate.source_state_penalty
                record.structural_score = candidate.structural_score
                record.candidate_score = candidate.structural_score
                record.risk_level = candidate.risk_level
                record.precondition_status = candidate_status
                if candidate_status == "ready_for_dry_run":
                    recipe_name = f"generated.architecture.P{index}_MoveMethod_{safe_fragment(candidate.source_member)}"
                    recipe_file = recipe_dir / f"prediction-{index:04d}-move-method-{safe_fragment(candidate.source_member).lower()}.yml"
                    recipe_file.write_text(move_method_recipe_yaml(
                        recipe_name, candidate.source_class, candidate.source_signature,
                        candidate.destination_class, f"Generated from prediction {index}: {candidate.reason}."
                    ), encoding="utf-8")
                    claimed_methods.add((candidate.source_class, candidate.source_signature))
                    candidate_count += 1
                    record.recipe_name = recipe_name
                    record.recipe_file = str(recipe_file.relative_to(output_dir))
        else:
            semantic_ranked = rank_semantic_move_classes(semantic_methods, affected_set)
            if semantic_ranked:
                ranked = [(types[source], destination, score, reason)
                          for source, destination, score, reason in semantic_ranked if source in types]
                unresolved_reason = "semantic reciprocal dependencies yielded no unused candidate"
                move_class_analysis_source = "semantic"
            else:
                ranked, unresolved_reason = rank_move_classes(affected_set, types)
                move_class_analysis_source = "import_fallback"

            def eligible(item: tuple[JavaType, str, float, str]) -> bool:
                source, destination, _, _ = item
                destination_modules = {
                    candidate.module for candidate in types.values()
                    if candidate.package == destination
                }
                destination_source_sets = {
                    candidate.source_set for candidate in types.values()
                    if candidate.package == destination
                }
                destination_type = f"{destination}.{source.simple_name}"
                return (
                    source.module in destination_modules
                    and source.source_set in destination_source_sets
                    and not (source.source_set == "main" and
                             (is_test_namespace(destination) or "main" not in destination_source_sets))
                    and destination_type not in types
                )

            eligible_ranked = [item for item in ranked if eligible(item)]
            selected = next(
                (item for item in eligible_ranked if item[0].qualified_name not in claimed_sources),
                None,
            )
            record.reason = unresolved_reason
            if selected:
                source, destination_package, score, reason = selected
                destination_type = f"{destination_package}.{source.simple_name}"
                record.source_type = source.qualified_name
                record.destination_type = destination_type
                record.source_package = source.package
                record.destination_package = destination_package
                record.candidate_score = score
                record.risk_level = "high_public_api" if source.is_public else "normal"
                record.refactoring_kind = "Move Class"
                record.analysis_source = move_class_analysis_source
                record.structural_score = score
                record.precondition_status = "ready_for_dry_run"
                recipe_name = (
                    f"generated.architecture.P{index}_Move_{safe_fragment(source.simple_name)}"
                )
                recipe_file = recipe_dir / f"prediction-{index:04d}-move-{safe_fragment(source.simple_name).lower()}.yml"
                recipe_file.write_text(
                    recipe_yaml(
                        recipe_name,
                        source.qualified_name,
                        destination_type,
                        f"Generated from prediction {index}: {reason}.",
                    ),
                    encoding="utf-8",
                )
                claimed_sources[source.qualified_name] = index
                candidate_count += 1
                record.status = "ready_for_dry_run"
                record.reason = reason
                record.recipe_name = recipe_name
                record.recipe_file = str(recipe_file.relative_to(output_dir))
            elif eligible_ranked:
                owners = sorted({claimed_sources[item[0].qualified_name] for item in eligible_ranked})
                record.status = "duplicate"
                record.reason = f"all ranked source types already selected by predictions {owners[:8]}"
            elif ranked:
                record.status = "unsafe_destination"
                record.reason = "ranked moves were cross-module, production-to-test, or destination conflicts"

        records.append(record)

    manifest = {
        "repository": str(repository),
        "predictions": str(args.predictions.resolve()),
        "java_types": len(types),
        "packages": len(packages),
        "record_count": len(records),
        "status_counts": dict(Counter(record.status for record in records)),
        "severity_counts": dict(Counter(record.severity for record in records)),
        "selected_severity_categories": sorted(selected_severities),
        "aggregate_recipe_name": "generated.architecture.ApplyAllCandidates",
        "aggregate_recipe_file": "all-candidates.yml",
        "records": [asdict(record) for record in records],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output_dir / "all-candidates.yml").write_text(aggregate_recipe_yaml(records), encoding="utf-8")

    fields = list(asdict(records[0]).keys()) if records else list(ManifestRecord.__annotations__)
    with (output_dir / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = asdict(record)
            row["affected_elements"] = args.elements_separator.join(row["affected_elements"])
            writer.writerow(row)

    print(json.dumps({key: value for key, value in manifest.items() if key != "records"}, indent=2))
    print(f"Manifest: {output_dir / 'manifest.json'}")
    print(f"Recipes:  {recipe_dir}")
    print(f"Aggregate: {output_dir / 'all-candidates.yml'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--smell-column", default="architecture_smell")
    parser.add_argument("--elements-column", default="affected_elements")
    parser.add_argument("--suggestions-column", default="suggestions")
    parser.add_argument("--elements-separator", default="|")
    parser.add_argument("--semantic-analysis", type=Path,
                        help="OpenRewrite semantic dependency JSON; import analysis remains Move Class fallback")
    parser.add_argument(
        "--severity-categories", default="high,medium,low",
        help="comma-separated categories to process: high,medium,low (default: all)",
    )
    args = parser.parse_args()
    categories = {item.strip().lower() for item in args.severity_categories.split(",") if item.strip()}
    if not categories or not categories.issubset({"high", "medium", "low"}):
        parser.error("--severity-categories must contain high, medium and/or low")
    args.severity_categories = ",".join(categories)
    generate(args)


if __name__ == "__main__":
    main()
