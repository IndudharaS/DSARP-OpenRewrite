#!/usr/bin/env python3
"""Convert OpenRewrite's method-dependency DataTable CSV to resolver JSON."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def truth(value: str) -> bool:
    return str(value).lower() == "true"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_rows = []
    for source in args.input:
        with source.open(newline="", encoding="utf-8-sig") as handle:
            raw_rows.extend(csv.DictReader(handle))
    aliases = {
        "sourceclass": "sourceClass", "sourcemethod": "sourceMethod", "signature": "signature",
        "sourcepackage": "sourcePackage", "path": "path", "visibility": "visibility",
        "static": "isStatic", "isstatic": "isStatic", "abstract": "isAbstract", "isabstract": "isAbstract",
        "native": "isNative", "isnative": "isNative", "synchronized": "isSynchronized",
        "issynchronized": "isSynchronized", "returntype": "returnType", "targettype": "targetType",
        "targetpackage": "targetPackage", "kind": "kind", "sourcestate": "sourceState",
    }
    rows = []
    for raw in raw_rows:
        row = {}
        for key, value in raw.items():
            normalized = "".join(character for character in key.lower() if character.isalnum())
            row[aliases.get(normalized, key)] = value
        rows.append(row)
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    metadata = {}
    for row in rows:
        key = (row["sourceClass"], row["signature"])
        grouped[key].append(row)
        metadata[key] = row
    methods = []
    for key in sorted(grouped):
        row = metadata[key]
        path = row["path"].replace("\\", "/")
        parts = path.split("/")
        src = parts.index("src") if "src" in parts else -1
        module = "/".join(parts[:src]) or "." if src >= 0 else "."
        source_set = parts[src + 1] if src >= 0 and len(parts) > src + 1 else "unknown"
        deps = Counter((item["targetType"], item["targetPackage"], item["kind"])
                       for item in grouped[key] if item.get("targetType"))
        methods.append({
            "sourceClass": row["sourceClass"], "sourceMethod": row["sourceMethod"],
            "signature": row["signature"], "sourcePackage": row["sourcePackage"],
            "path": path, "module": module, "sourceSet": source_set,
            "visibility": row["visibility"], "static": truth(row["isStatic"]),
            "abstract": truth(row["isAbstract"]), "native": truth(row["isNative"]),
            "synchronized": truth(row["isSynchronized"]), "returnType": row["returnType"],
            "sourceStateReferences": sum(truth(item.get("sourceState", "")) for item in grouped[key]),
            "dependencies": [{"targetType": target, "targetPackage": package, "kind": kind, "count": count}
                             for (target, package, kind), count in sorted(deps.items())],
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schemaVersion": 1, "methods": methods}, indent=2) + "\n", encoding="utf-8")
    print(f"Semantic methods: {len(methods)}; output: {args.output}")


if __name__ == "__main__":
    main()
