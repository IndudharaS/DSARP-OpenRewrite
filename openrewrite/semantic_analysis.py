"""Load machine-readable dependency evidence emitted by OpenRewrite analysis."""

from __future__ import annotations

import json
from pathlib import Path

from openrewrite.candidate_models import JavaMethod, MethodDependency


def load_methods(path: Path | None) -> list[JavaMethod]:
    if path is None or not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("methods", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Semantic analysis must contain a JSON list of methods")
    methods = []
    for row in rows:
        dependencies = tuple(MethodDependency(
            target_type=str(item["targetType"]),
            target_package=str(item["targetPackage"]),
            dependency_kind=str(item["kind"]),
            count=int(item.get("count", 1)),
        ) for item in row.get("dependencies", []))
        methods.append(JavaMethod(
            qualified_owner=str(row["sourceClass"]), name=str(row["sourceMethod"]),
            signature=str(row["signature"]), return_type=row.get("returnType"),
            parameters=tuple(row.get("parameters", [])), path=str(row["path"]),
            package=str(row["sourcePackage"]), module=str(row.get("module", ".")),
            source_set=str(row.get("sourceSet", "unknown")),
            visibility=str(row.get("visibility", "package")),
            is_static=bool(row.get("static")), is_abstract=bool(row.get("abstract")),
            is_native=bool(row.get("native")), is_synchronized=bool(row.get("synchronized")),
            source_state_references=int(row.get("sourceStateReferences", 0)),
            dependencies=dependencies,
        ))
    return methods

