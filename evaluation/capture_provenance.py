#!/usr/bin/env python3
"""Capture immutable experiment and tool provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def command(*values: str) -> str:
    process = subprocess.run(values, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return process.stdout.strip().splitlines()[0] if process.stdout.strip() else "unavailable"


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--java", type=Path, required=True)
    parser.add_argument("--arcan-jar", type=Path, required=True)
    parser.add_argument("--refactoring-miner", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "repository_url": args.repository_url,
        "version_id": args.version_id,
        "profile": args.profile,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "git": command("git", "--version"),
        "java": command(str(args.java), "-version"),
        "maven_test_pattern": os.environ.get("DSARP_MAVEN_TEST_PATTERN"),
        "tools": {
            "arcan_jar": {"path": str(args.arcan_jar), "sha256": digest(args.arcan_jar)},
            "refactoring_miner": {"path": str(args.refactoring_miner)},
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
