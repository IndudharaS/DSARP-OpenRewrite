from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", re.M)
IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([A-Za-z0-9_.]+)", re.M)
TYPE_RE = re.compile(r"\b(?:class|interface|enum|record)\s+[A-Za-z_][A-Za-z0-9_]*")


SELECTED_SMELLS = {
    "Cyclic Dependency",
    "Hub-like Dependency",
    "Unstable Dependency",
}

ARCHITECTURE_RELEVANT_LABELS = {
    "Move Class",
    "Move Method",
    "Move Attribute",
    "Move And Rename Class",
    "Move And Rename Method",
    "Extract Class",
    "Extract Method",
    "Extract Interface",
    "Pull Up Method",
    "Pull Up Attribute",
    "Push Down Method",
    "Push Down Attribute",
    "Extract Superclass",
    "Extract Subclass",
    "Extract And Move Method",
    "Split Package",
    "Move Source Folder",
    "Rename Package",
}


REPOSITORIES = [
    {"name": "tika", "url": "https://github.com/apache/tika.git"},
    {"name": "maven", "url": "https://github.com/apache/maven.git"},
    {"name": "camel", "url": "https://github.com/apache/camel.git"},
    {"name": "ant", "url": "https://github.com/apache/ant.git"},
    {"name": "lucene", "url": "https://github.com/apache/lucene.git"},
]


@dataclass
class VersionMetrics:
    java_files: int
    packages: int
    package_edges: int
    cyclic_packages: int
    max_fan_in: int
    max_fan_out: int
    unstable_dependencies: int
    hub_like_packages: int
    large_packages: int
    median_package_size: float


def run(cmd, cwd=None, check=True):
    process = subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )

    if check and process.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            f"cmd: {cmd}\n"
            f"cwd: {cwd}\n"
            f"returncode: {process.returncode}\n"
            f"stdout:\n{process.stdout}\n"
            f"stderr:\n{process.stderr}"
        )

    return process.stdout.strip()


def checkout(repo, commit):
    repo = Path(repo)
    lock_file = repo / ".git" / "index.lock"

    if lock_file.exists():
        lock_file.unlink()

    run(["git", "reset", "--hard", "HEAD"], cwd=repo)
    run(["git", "clean", "-fd"], cwd=repo)
    run(["git", "checkout", "--force", "--quiet", commit], cwd=repo)


def clone_or_update(repos, repos_dir):
    repos_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    run(["git", "config", "--global", "core.longpaths", "true"], check=False)

    for repo in repos:
        target = repos_dir / repo["name"]

        if target.exists() and not (target / ".git").exists():
            shutil.rmtree(target)

        if target.exists():
            print(f"Updating {repo['name']}")
            run(["git", "fetch", "--all", "--prune"], cwd=target)
        else:
            print(f"Cloning {repo['name']}")
            run(["git", "clone", repo["url"], str(target)])

        commits = int(run(["git", "rev-list", "--count", "HEAD"], cwd=target))
        rows.append({
            "repository": repo["name"],
            "url": repo["url"],
            "path": str(target),
            "commits": commits,
        })

    return pd.DataFrame(rows)


def candidate_commits(repo, limit):
    terms = [
        "refactor",
        "extract",
        "move",
        "rename",
        "inline",
        "clean",
        "decompos",
        "modular",
        "architecture",
        "dependency",
    ]

    cmd = ["git", "log", "--date-order", "--reverse", "--regexp-ignore-case", "--format=%H"]

    for term in terms:
        cmd += ["--grep", term]

    commits = list(dict.fromkeys(run(cmd, cwd=repo, check=False).splitlines()))

    if limit is None or limit < 0:
        return commits

    return commits[:limit]


def commit_metadata(repo, commit):
    raw = run(["git", "show", "-s", "--format=%H%n%P%n%aI%n%s", commit], cwd=repo)
    lines = raw.splitlines()

    return {
        "commit": lines[0],
        "parents": lines[1].split() if len(lines) > 1 and lines[1] else [],
        "date": lines[2] if len(lines) > 2 else "",
        "subject": lines[3] if len(lines) > 3 else "",
    }


def get_refactorings(refminer, repo, commit, cache_file):
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if not cache_file.exists():
        run([
            str(refminer),
            "-c",
            str(Path(repo).resolve()),
            commit,
            "-json",
            str(Path(cache_file).resolve()),
        ])

    data = json.loads(cache_file.read_text(encoding="utf-8"))
    commits = data.get("commits", [])

    if not commits:
        return []

    return commits[0].get("refactorings", [])


def refactoring_labels(refactorings):
    return sorted({
        r.get("type", "").strip()
        for r in refactorings
        if r.get("type")
    })


def refactoring_touch_text(refactoring):
    parts = [
        refactoring.get("description", ""),
        refactoring.get("type", ""),
    ]

    for side in ["leftSideLocations", "rightSideLocations"]:
        for loc in refactoring.get(side, []) or []:
            parts.append(str(loc.get("codeElement", "")))
            parts.append(str(loc.get("filePath", "")))

    return " ".join(parts)


def compact_refactoring_details(refactorings):
    details = []

    for refactoring in refactorings:
        label = refactoring.get("type", "").strip()

        if not label:
            continue

        details.append({
            "type": label,
            "touch_text": refactoring_touch_text(refactoring),
        })

    return details


def java_files(repo):
    ignored = {".git", "target", "build", ".gradle", "out", ".idea"}

    for path in Path(repo).rglob("*.java"):
        if not any(part in ignored for part in path.parts):
            yield path


def package_of(source):
    match = PACKAGE_RE.search(source)
    return match.group(1) if match else "<default>"


def strongly_connected_components(graph):
    index = 0
    stack = []
    indices = {}
    lows = {}
    on_stack = set()
    components = []

    def visit(node):
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
            component = set()

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


def package_graph_snapshot(repo, commit):
    checkout(repo, commit)

    package_sizes = Counter()
    package_graph = defaultdict(set)
    java_count = 0

    for file_path in java_files(repo):
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        java_count += 1

        package = package_of(source)
        package_sizes[package] += max(1, len(TYPE_RE.findall(source)))
        package_graph.setdefault(package, set())

        for imported in IMPORT_RE.findall(source):
            parts = imported.split(".")

            if len(parts) >= 3:
                imported_package = ".".join(parts[:-1])

                if imported_package != package:
                    package_graph[package].add(imported_package)
                    package_graph.setdefault(imported_package, set())

    fan_out = {pkg: len(deps) for pkg, deps in package_graph.items()}
    fan_in = Counter(dep for deps in package_graph.values() for dep in deps)
    all_packages = set(package_graph) | set(fan_in)

    sizes = sorted(package_sizes.values())
    median = float(sizes[len(sizes) // 2]) if sizes else 0.0
    large_threshold = max(10.0, median * 2.0)

    cycles = [
        component
        for component in strongly_connected_components(package_graph)
        if len(component) > 1
    ]

    unstable = []
    hubs = []

    for package in all_packages:
        incoming = fan_in.get(package, 0)
        outgoing = fan_out.get(package, 0)
        total = incoming + outgoing
        instability = outgoing / total if total else 0.0

        if incoming >= 8 and outgoing >= 8:
            hubs.append(package)

        if incoming >= 5 and instability > 0.8:
            unstable.append(package)

    metrics = VersionMetrics(
        java_files=java_count,
        packages=len(package_sizes),
        package_edges=sum(fan_out.values()),
        cyclic_packages=sum(len(component) for component in cycles),
        max_fan_in=max(fan_in.values()) if fan_in else 0,
        max_fan_out=max(fan_out.values()) if fan_out else 0,
        unstable_dependencies=len(unstable),
        hub_like_packages=len(hubs),
        large_packages=sum(1 for value in package_sizes.values() if value >= large_threshold),
        median_package_size=median,
    )

    return {
        "metrics": metrics,
        "package_graph": package_graph,
        "fan_in": fan_in,
        "fan_out": fan_out,
        "package_sizes": package_sizes,
        "cycles": cycles,
        "hubs": sorted(hubs),
        "unstable": sorted(unstable),
    }


def cycle_edges(component, graph):
    component = set(component)
    return sum(
        1
        for package in component
        for dep in graph.get(package, set())
        if dep in component
    )


def smell_instances_from_snapshot(snapshot):
    rows = []
    metrics = snapshot["metrics"]
    graph = snapshot["package_graph"]
    fan_in = snapshot["fan_in"]
    fan_out = snapshot["fan_out"]

    for index, component in enumerate(snapshot["cycles"], start=1):
        affected = sorted(component)
        rows.append({
            "architecture_smell": "Cyclic Dependency",
            "smellType": "cyclicDep",
            "vertexId": f"cyclicDep:{index}",
            "vertexLabel": "|".join(affected),
            "AffectedElements": "[" + ", ".join(affected) + "]",
            "affected_elements": "|".join(affected),
            "AffectedComponentType": "CONTAINER",
            "AffectedConstructType": "PACKAGE",
            "Severity": float(len(affected)),
            "Size": len(affected),
            "Shape": "CYCLE",
            "NumberOfEdges": cycle_edges(affected, graph),
            "SmellExtent": len(affected),
            "Strength": 0.0,
            "ATDI": float(len(affected)),
            "ATDI_WEIGHTED": float(len(affected)),
            "InstabilityGap": 0.0,
            "LOCDensity": 0.0,
        })

    for package in snapshot["hubs"]:
        incoming = fan_in.get(package, 0)
        outgoing = fan_out.get(package, 0)
        rows.append({
            "architecture_smell": "Hub-like Dependency",
            "smellType": "hubLikeDep",
            "vertexId": f"hubLikeDep:{package}",
            "vertexLabel": package,
            "AffectedElements": f"[{package}]",
            "affected_elements": package,
            "AffectedComponentType": "CONTAINER",
            "AffectedConstructType": "PACKAGE",
            "Severity": float(incoming + outgoing),
            "Size": 1,
            "Shape": "HUB",
            "NumberOfEdges": int(incoming + outgoing),
            "SmellExtent": 1,
            "Strength": float(min(incoming, outgoing)),
            "ATDI": float(incoming + outgoing),
            "ATDI_WEIGHTED": float(incoming + outgoing),
            "InstabilityGap": 0.0,
            "LOCDensity": 0.0,
        })

    for package in snapshot["unstable"]:
        incoming = fan_in.get(package, 0)
        outgoing = fan_out.get(package, 0)
        total = incoming + outgoing
        instability = outgoing / total if total else 0.0

        rows.append({
            "architecture_smell": "Unstable Dependency",
            "smellType": "unstableDep",
            "vertexId": f"unstableDep:{package}",
            "vertexLabel": package,
            "AffectedElements": f"[{package}]",
            "affected_elements": package,
            "AffectedComponentType": "CONTAINER",
            "AffectedConstructType": "PACKAGE",
            "Severity": float(instability),
            "Size": 1,
            "Shape": "UNSTABLE",
            "NumberOfEdges": int(incoming + outgoing),
            "SmellExtent": 1,
            "Strength": float(instability),
            "ATDI": float(incoming + outgoing),
            "ATDI_WEIGHTED": float(instability),
            "InstabilityGap": float(max(0.0, instability - 0.8)),
            "LOCDensity": 0.0,
        })

    for row in rows:
        for field, value in metrics.__dict__.items():
            row[field] = value

    return rows


def overlap_score(affected_elements, touch_text):
    affected = [
        item.strip()
        for item in str(affected_elements).split("|")
        if item.strip()
    ]

    if not affected:
        return 0.0

    touch_text = str(touch_text).lower().replace("\\", "/")
    matches = 0

    for element in affected:
        element_lower = element.lower()
        path_form = element_lower.replace(".", "/")

        if element_lower in touch_text or path_form in touch_text:
            matches += 1
            continue

        simple_name = element_lower.split(".")[-1]
        if simple_name and simple_name in touch_text:
            matches += 1

    return matches / len(affected)


def select_labels_for_row(row, refactoring_details, top_k):
    candidates = []

    for item in refactoring_details:
        label = item["type"]

        if label not in ARCHITECTURE_RELEVANT_LABELS:
            continue

        overlap = overlap_score(row["affected_elements"], item["touch_text"])

        candidates.append({
            "label": label,
            "overlap_score": overlap,
        })

    best_by_label = {}

    for item in candidates:
        label = item["label"]
        best = best_by_label.get(label)

        if best is None or item["overlap_score"] > best["overlap_score"]:
            best_by_label[label] = item

    ranked = sorted(
        best_by_label.values(),
        key=lambda item: (-item["overlap_score"], item["label"]),
    )

    positive = [item for item in ranked if item["overlap_score"] > 0]

    if positive:
        selected = positive[:top_k]
    else:
        selected = ranked[:top_k]

    return {
        "selected_refactoring_labels": "|".join(item["label"] for item in selected),
        "selected_label_count": len(selected),
        "ranked_label_details_json": json.dumps(ranked),
    }


def smell_signature(row):
    return (
        row["architecture_smell"],
        tuple(sorted(str(row["affected_elements"]).split("|"))),
    )


def smell_removed_or_reduced(before_row, after_rows):
    before_signature = smell_signature(before_row)
    after_by_signature = {
        smell_signature(row): row
        for row in after_rows
    }

    after_match = after_by_signature.get(before_signature)

    if after_match is None:
        return {
            "historical_improvement_reason": "smell_removed_or_affected_elements_changed",
            "historically_improved": True,
        }

    before_severity = float(before_row.get("Severity", 0) or 0)
    after_severity = float(after_match.get("Severity", 0) or 0)
    before_size = float(before_row.get("Size", 0) or 0)
    after_size = float(after_match.get("Size", 0) or 0)
    before_edges = float(before_row.get("NumberOfEdges", 0) or 0)
    after_edges = float(after_match.get("NumberOfEdges", 0) or 0)

    improved = (
        after_severity < before_severity
        or after_size < before_size
        or after_edges < before_edges
    )

    return {
        "historical_improvement_reason": "severity_size_or_edges_reduced" if improved else "not_reduced",
        "historically_improved": bool(improved),
    }


def build_arcan_style_input_text(row):
    affected = str(row.get("affected_elements", "")).replace("|", ", ")

    parts = [
        f"Architecture smell: {row['architecture_smell']}.",
        f"Smell type: {row.get('smellType', '')}.",
        f"Affected component type: {row.get('AffectedComponentType', '')}.",
        f"Affected construct type: {row.get('AffectedConstructType', '')}.",
        f"Affected elements: {affected}.",
        f"Severity: {row.get('Severity', 0)}.",
        f"Size: {row.get('Size', 0)}.",
        f"Shape: {row.get('Shape', '')}.",
        f"NumberOfEdges: {row.get('NumberOfEdges', 0)}.",
        f"SmellExtent: {row.get('SmellExtent', 0)}.",
        f"Strength: {row.get('Strength', 0)}.",
        f"ATDI: {row.get('ATDI', 0)}.",
        f"ATDI_WEIGHTED: {row.get('ATDI_WEIGHTED', 0)}.",
        f"InstabilityGap: {row.get('InstabilityGap', 0)}.",
        f"LOCDensity: {row.get('LOCDensity', 0)}.",
        f"Packages: {row.get('packages', 0)}.",
        f"Package edges: {row.get('package_edges', 0)}.",
        f"Cyclic packages: {row.get('cyclic_packages', 0)}.",
        f"Max fan in: {row.get('max_fan_in', 0)}.",
        f"Max fan out: {row.get('max_fan_out', 0)}.",
        f"Unstable dependencies: {row.get('unstable_dependencies', 0)}.",
        f"Hub-like packages: {row.get('hub_like_packages', 0)}.",
        f"Large packages: {row.get('large_packages', 0)}.",
        f"Median package size: {row.get('median_package_size', 0)}.",
    ]

    return " ".join(parts)


def mine_repository(repo_config, work_dir, refminer, max_commits_per_repo, top_k):
    repo_name = repo_config["name"]
    repo_path = work_dir / "repositories" / repo_name
    cache_dir = work_dir / "refactoringminer" / repo_name
    original_head = run(["git", "rev-parse", "HEAD"], cwd=repo_path)
    commits = candidate_commits(repo_path, max_commits_per_repo)
    raw_commit_rows = []
    raw_smell_rows = []
    training_rows = []
    errors = []

    print(f"{repo_name}: {len(commits)} candidate commits")

    try:
        for index, commit in enumerate(commits, start=1):
            print(f"{repo_name} {index}/{len(commits)} {commit[:12]}")

            try:
                meta = commit_metadata(repo_path, commit)

                if len(meta["parents"]) != 1:
                    continue

                parent = meta["parents"][0]

                refactorings = get_refactorings(
                    refminer,
                    repo_path,
                    commit,
                    cache_dir / f"{commit}.json",
                )

                labels = refactoring_labels(refactorings)
                details = compact_refactoring_details(refactorings)

                raw_commit_rows.append({
                    "repository": repo_name,
                    "commit": commit,
                    "parent_commit": parent,
                    "author_date": meta["date"],
                    "subject": meta["subject"],
                    "all_refactoring_labels": "|".join(labels),
                    "refactoring_count": len(refactorings),
                    "refactoring_details_json": json.dumps(details),
                })

                if not labels:
                    continue

                before_snapshot = package_graph_snapshot(repo_path, parent)
                after_snapshot = package_graph_snapshot(repo_path, commit)
                before_smells = smell_instances_from_snapshot(before_snapshot)
                after_smells = smell_instances_from_snapshot(after_snapshot)

                for smell_row in before_smells:
                    improvement = smell_removed_or_reduced(smell_row, after_smells)
                    label_selection = select_labels_for_row(smell_row, details, top_k)

                    raw_row = {
                        "repository": repo_name,
                        "commit": commit,
                        "parent_commit": parent,
                        "author_date": meta["date"],
                        "subject": meta["subject"],
                        "all_refactoring_labels": "|".join(labels),
                        "refactoring_details_json": json.dumps(details),
                        **improvement,
                        **label_selection,
                        **smell_row,
                    }

                    raw_smell_rows.append(raw_row)

                    if not improvement["historically_improved"]:
                        continue

                    if label_selection["selected_label_count"] == 0:
                        continue

                    train_row = dict(raw_row)
                    train_row["input_text"] = build_arcan_style_input_text(train_row)
                    train_row["target_text"] = train_row["selected_refactoring_labels"].replace("|", " | ")
                    training_rows.append(train_row)

            except Exception as exc:
                errors.append({
                    "repository": repo_name,
                    "commit": commit,
                    "error": str(exc)[:2000],
                })

                try:
                    checkout(repo_path, original_head)
                except Exception as cleanup_exc:
                    errors.append({
                        "repository": repo_name,
                        "commit": commit,
                        "error": f"cleanup_failed: {str(cleanup_exc)[:2000]}",
                    })

    finally:
        checkout(repo_path, original_head)

    return (
        pd.DataFrame(raw_commit_rows),
        pd.DataFrame(raw_smell_rows),
        pd.DataFrame(training_rows),
        pd.DataFrame(errors),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", default=r"C:\dsarp_work_arcan_matched")
    parser.add_argument("--output-dir", default=r"C:\dsarp_outputs\arcan_matched_dataset")
    parser.add_argument("--refactoring-miner", default=os.environ.get("REFACTORING_MINER_BIN", r"C:\rm\bin\RefactoringMiner.bat"))
    parser.add_argument("--max-commits-per-repo", type=int, default=100)
    parser.add_argument("--top-k-labels", type=int, default=5)
    args = parser.parse_args()

    work_dir = Path(args.work_dir)
    output_dir = Path(args.output_dir)
    refminer = Path(args.refactoring_miner)

    output_dir.mkdir(parents=True, exist_ok=True)
    repo_summary = clone_or_update(REPOSITORIES, work_dir / "repositories")

    commit_parts = []
    smell_parts = []
    train_parts = []
    error_parts = []

    for repo in REPOSITORIES:
        raw_commits, raw_smells, training_rows, errors = mine_repository(
            repo,
            work_dir,
            refminer,
            args.max_commits_per_repo,
            args.top_k_labels,
        )

        commit_parts.append(raw_commits)
        smell_parts.append(raw_smells)
        train_parts.append(training_rows)
        error_parts.append(errors)

    raw_commits = pd.concat(commit_parts, ignore_index=True) if commit_parts else pd.DataFrame()
    raw_smells = pd.concat(smell_parts, ignore_index=True) if smell_parts else pd.DataFrame()
    training_dataset = pd.concat(train_parts, ignore_index=True) if train_parts else pd.DataFrame()
    errors = pd.concat(error_parts, ignore_index=True) if error_parts else pd.DataFrame()

    raw_commits.to_csv(output_dir / "raw_commits.csv", index=False)
    raw_commits.to_json(output_dir / "raw_commits.jsonl", orient="records", lines=True)
    raw_smells.to_csv(output_dir / "raw_smell_instances.csv", index=False)
    raw_smells.to_json(output_dir / "raw_smell_instances.jsonl", orient="records", lines=True)
    training_dataset.to_csv(output_dir / "arcan_style_training_dataset.csv", index=False)
    training_dataset.to_json(output_dir / "arcan_style_training_dataset.jsonl", orient="records", lines=True)
    errors.to_csv(output_dir / "mining_errors.csv", index=False)
    repo_summary.to_csv(output_dir / "repository_summary.csv", index=False)

    print("Saved to:", output_dir.resolve())
    print("Raw commits:", len(raw_commits))
    print("Raw smell instances:", len(raw_smells))
    print("Training rows:", len(training_dataset))
    print("Errors:", len(errors))

    if not training_dataset.empty:
        print("\nSmell counts:")
        print(training_dataset["architecture_smell"].value_counts())
        print("\nSelected label counts:")
        print(training_dataset["selected_refactoring_labels"].str.split("|").explode().value_counts().head(30))


if __name__ == "__main__":
    main()
