"""Semantic Move Class ranking with the legacy import resolver as caller fallback."""

from __future__ import annotations

from collections import Counter, defaultdict

from openrewrite.candidate_models import JavaMethod


def rank_semantic_move_classes(methods: list[JavaMethod], affected: set[str]) -> list[tuple[str, str, float, str]]:
    edges: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for method in methods:
        if method.package not in affected:
            continue
        for dependency in method.dependencies:
            if dependency.target_package in affected and dependency.target_package != method.package:
                edges[(method.package, dependency.target_package)][dependency.target_type] += dependency.count
    candidates = []
    for (source_package, target_package), targets in sorted(edges.items()):
        reciprocal = (target_package, source_package) in edges
        direction_total = sum(targets.values())
        for target_type, count in targets.items():
            score = round(100.0 * count / max(direction_total, 1), 4)
            reason = (f"ranked semantic {'reciprocal' if reciprocal else 'one-way'} dependency candidate; "
                      f"resolved references={count}, direction references={direction_total}")
            # Reciprocal package edges are strongest evidence for a cycle, but
            # one-way edges are still useful for hub/unstable smells and often
            # expose safer internal types missed by the old resolver.
            candidates.append((0 if reciprocal else 1, -score, target_type,
                               source_package, score, reason))
    return [(target, destination, score, reason)
            for _, _, target, destination, score, reason in sorted(candidates)]
