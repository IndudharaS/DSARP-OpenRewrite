#!/usr/bin/env python3
"""Repeatable package-level smell measurement used for before/after comparison."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", re.M)
IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([A-Za-z0-9_.]+)", re.M)
TYPE_RE = re.compile(r"\b(?:class|interface|enum|record)\s+[A-Za-z_][A-Za-z0-9_]*")
IGNORED_PARTS = {".git", "target", "build", ".gradle", "out", ".idea"}


def strongly_connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lows: dict[str, int] = {}
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lows[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in graph.get(node, set()):
            if neighbor not in indices:
                visit(neighbor)
                lows[node] = min(lows[node], lows[neighbor])
            elif neighbor in on_stack:
                lows[node] = min(lows[node], indices[neighbor])
        if lows[node] == indices[node]:
            component: set[str] = set()
            while True:
                item = stack.pop()
                on_stack.remove(item)
                component.add(item)
                if item == node:
                    break
            components.append(component)

    for node in graph:
        if node not in indices:
            visit(node)
    return components


def analyze(repository: Path) -> dict[str, object]:
    graph: dict[str, set[str]] = defaultdict(set)
    package_sizes: Counter[str] = Counter()
    java_files = 0
    for path in repository.rglob("*.java"):
        if any(part in IGNORED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        match = PACKAGE_RE.search(source)
        package = match.group(1) if match else "<default>"
        java_files += 1
        package_sizes[package] += max(1, len(TYPE_RE.findall(source)))
        graph.setdefault(package, set())
        for imported in IMPORT_RE.findall(source):
            parts = imported.split(".")
            if len(parts) >= 3:
                imported_package = ".".join(parts[:-1])
                if imported_package != package:
                    graph[package].add(imported_package)
                    graph.setdefault(imported_package, set())

    fan_out = {package: len(dependencies) for package, dependencies in graph.items()}
    fan_in = Counter(dependency for dependencies in graph.values() for dependency in dependencies)
    packages = set(graph) | set(fan_in)
    cycles = [sorted(component) for component in strongly_connected_components(graph) if len(component) > 1]
    hubs = sorted(package for package in packages if fan_in[package] >= 8 and fan_out.get(package, 0) >= 8)
    unstable = sorted(
        package
        for package in packages
        if fan_in[package] >= 5
        and fan_out.get(package, 0) / (fan_in[package] + fan_out.get(package, 0)) > 0.8
    )
    sizes = sorted(package_sizes.values())
    median = float(sizes[len(sizes) // 2]) if sizes else 0.0
    target_packages = {
        "org.apache.logging.log4j.core.appender.rolling",
        "org.apache.logging.log4j.core.appender.rolling.action",
    }
    target_cycle = next((cycle for cycle in cycles if target_packages <= set(cycle)), None)
    target_edges = {
        package: sorted(dependency for dependency in graph.get(package, set()) if dependency in target_packages)
        for package in sorted(target_packages)
    }
    target_direct_cycle_present = all(
        (target_packages - {package}) <= set(target_edges[package]) for package in target_packages
    )
    return {
        "java_files": java_files,
        "source_packages": len(package_sizes),
        "graph_nodes": len(packages),
        "package_edges": sum(fan_out.values()),
        "cycle_components": len(cycles),
        "cyclic_packages": sum(len(component) for component in cycles),
        "max_fan_in": max(fan_in.values(), default=0),
        "max_fan_out": max(fan_out.values(), default=0),
        "unstable_dependencies": len(unstable),
        "hub_like_packages": len(hubs),
        "large_packages": sum(1 for size in package_sizes.values() if size >= max(10.0, median * 2.0)),
        "median_package_size": median,
        "target_cycle_present": target_cycle is not None,
        "target_direct_cycle_present": target_direct_cycle_present,
        "target_cycle_component": target_cycle,
        "target_edges": target_edges,
        "cycles": cycles,
        "hubs": hubs,
        "unstable": unstable,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.repository.resolve())
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
