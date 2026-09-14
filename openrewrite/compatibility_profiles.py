"""Load project-specific public-API compatibility strategies from data files."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


PROFILE_DIRECTORY = Path(__file__).with_name("profiles")


def _normalize_repository(value: str) -> str:
    return value.strip().lower().rstrip("/").removesuffix(".git")


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


def profile_for_target(system: str, repository_url: str) -> str:
    """Select a registered project profile, or ``none`` for an unknown repo."""
    normalized_system = system.strip().lower()
    normalized_repository = _normalize_repository(repository_url)
    for path in sorted(PROFILE_DIRECTORY.glob("*.json")):
        profile = load_profile(path.stem)
        systems = {str(value).strip().lower() for value in profile.get("systems", [])}
        repositories = {
            _normalize_repository(str(value)) for value in profile.get("repository_urls", [])
        }
        if normalized_system in systems or normalized_repository in repositories:
            return path.stem
    return "none"
