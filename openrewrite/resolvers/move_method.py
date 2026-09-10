"""Conservative semantic resolver for the supported static Move Method subset."""

from __future__ import annotations

from collections import Counter

from openrewrite.candidate_models import JavaMethod, MoveMethodCandidate


MIN_STRUCTURAL_SCORE = 0.70
MIN_FOREIGN_AFFINITY = 0.60


def resolve_move_method(
    methods: list[JavaMethod], affected_packages: set[str], existing_types: set[str],
    claimed: set[tuple[str, str]],
) -> MoveMethodCandidate | None:
    """Select the strongest deterministic candidate supported by the recipe.

    Version one intentionally executes only static, non-public methods. This
    allows exact call-site owner rewriting without inventing a receiver or
    changing method parameters.
    """
    candidates: list[tuple[float, str, str, MoveMethodCandidate]] = []
    declared_methods = {(method.qualified_owner, method.signature) for method in methods}
    for method in methods:
        if method.package not in affected_packages or method.name in {"<init>", "<clinit>"}:
            continue
        if "generated" in method.path.replace("\\", "/").split("/"):
            continue
        if (method.qualified_owner, method.signature) in claimed:
            continue
        if method.is_abstract or method.is_native or method.is_synchronized or not method.is_static:
            continue
        counts = Counter()
        total_external = 0
        for dependency in method.dependencies:
            if dependency.target_package == method.package:
                continue
            counts[dependency.target_type] += dependency.count
            total_external += dependency.count
        if not counts or not total_external:
            continue
        eligible_targets = [(target, count) for target, count in counts.items()
                            if target in existing_types and target.rsplit(".", 1)[0] in affected_packages]
        if not eligible_targets:
            continue
        target_type, target_count = sorted(eligible_targets, key=lambda item: (-item[1], item[0]))[0]
        destination_package = target_type.rsplit(".", 1)[0]
        foreign_count = sum(count for target, count in counts.items()
                            if target.rsplit(".", 1)[0] == destination_package)
        foreign_affinity = foreign_count / total_external
        destination_affinity = target_count / foreign_count
        total_relevant = total_external + method.source_state_references
        state_penalty = method.source_state_references / total_relevant if total_relevant else 0.0
        score = 0.50 * foreign_affinity + 0.30 * destination_affinity + 0.20 * (1 - state_penalty)
        unsupported_context = [dependency for dependency in method.dependencies
                               if dependency.target_package not in {destination_package, "java.lang"}
                               and dependency.target_type != method.qualified_owner]
        if (target_type, method.signature) in declared_methods:
            status, reason, risk = "destination_conflict", "destination already declares the same method signature", "conflict"
        elif method.visibility != "public":
            status, reason, risk = "unsupported_visibility", "cross-package static movement initially requires an already-public method", "visibility"
        elif method.source_state_references > 0:
            status, reason, risk = "unsafe_source_state", "method has a resolved dependency on source-class state", "source_state"
        elif foreign_affinity < MIN_FOREIGN_AFFINITY or score < MIN_STRUCTURAL_SCORE:
            status, reason, risk = "unresolved_destination", "semantic destination affinity is below the conservative threshold", "normal"
        elif unsupported_context:
            status, reason, risk = "unsupported_dependency_context", "method requires types outside the destination package", "dependency_context"
        else:
            status, reason, risk = "ready_for_dry_run", "semantic dependencies identify an existing destination class; public API validation is required", "high_public_api"
        candidate = MoveMethodCandidate(
            source_class=method.qualified_owner, source_member=method.name,
            source_signature=method.signature, destination_class=target_type,
            source_package=method.package, destination_package=destination_package,
            foreign_affinity=round(foreign_affinity, 6),
            destination_class_affinity=round(destination_affinity, 6),
            source_state_penalty=round(state_penalty, 6), structural_score=round(score, 6),
            risk_level=risk, status=status, reason=reason,
        )
        candidates.append((-score, method.qualified_owner, method.signature, candidate))
    return sorted(candidates, key=lambda item: item[:3])[0][3] if candidates else None
