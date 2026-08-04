#!/usr/bin/env python3
"""Validate the three baseline CSV files and their experiment identity."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


SCHEMAS = {
    "component-metrics.csv": {"project", "versionId", "ComponentType", "name"},
    "smell-characteristics.csv": {
        "project", "versionId", "smellType", "AffectedConstructType", "AffectedElements",
    },
    "smell-affects.csv": {"project", "versionId", "from", "to"},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"project": args.project, "version_id": args.version_id, "files": {}}
    for filename, required in SCHEMAS.items():
        path = args.directory / filename
        if not path.is_file():
            raise SystemExit(f"Missing baseline file: {path}")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            missing = sorted(required - fields)
            if missing:
                raise SystemExit(f"{filename} is missing columns: {', '.join(missing)}")
            rows = list(reader)
        if not rows:
            raise SystemExit(f"{filename} is empty")
        projects = {row.get("project", "") for row in rows if row.get("project")}
        versions = {row.get("versionId", "") for row in rows if row.get("versionId")}
        if projects != {args.project}:
            raise SystemExit(f"{filename} project values {sorted(projects)} != {args.project}")
        if versions != {args.version_id}:
            raise SystemExit(f"{filename} version values {sorted(versions)} != {args.version_id}")
        report["files"][filename] = {"records": len(rows), "columns": len(fields)}
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
