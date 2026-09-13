"""Load project-specific public-API compatibility strategies from data files."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


PROFILE_DIRECTORY = Path(__file__).with_name("profiles")


@lru_cache(maxsize=None)
def load_profile(name: str) -> dict[str, object]:
    if name == "none":
        return {"compatibility_strategies": []}
    path = PROFILE_DIRECTORY / f"{name}.json"
    if not path.is_file():
        return {"compatibility_strategies": []}
    return json.loads(path.read_text(encoding="utf-8"))


def compatibility_strategy(
    profile: str,
    source_type: str | None,
    destination_type: str | None,
) -> str | None:
    for entry in load_profile(profile).get("compatibility_strategies", []):
        if (entry.get("source_type") == source_type
                and entry.get("destination_type") == destination_type):
            return str(entry["strategy"])
    return None


def any_compatibility_strategy(
    source_type: str | None,
    destination_type: str | None,
) -> str | None:
    for path in sorted(PROFILE_DIRECTORY.glob("*.json")):
        strategy = compatibility_strategy(path.stem, source_type, destination_type)
        if strategy:
            return strategy
    return None
