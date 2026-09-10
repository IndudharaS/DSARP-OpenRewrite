"""Domain models shared by semantic candidate resolvers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MethodDependency:
    target_type: str
    target_package: str
    dependency_kind: str
    count: int = 1


@dataclass(frozen=True)
class JavaMethod:
    qualified_owner: str
    name: str
    signature: str
    return_type: str | None
    parameters: tuple[str, ...]
    path: str
    package: str
    module: str
    source_set: str
    visibility: str
    is_static: bool
    is_abstract: bool
    is_native: bool
    is_synchronized: bool
    source_state_references: int
    dependencies: tuple[MethodDependency, ...]


@dataclass(frozen=True)
class MoveMethodCandidate:
    source_class: str
    source_member: str
    source_signature: str
    destination_class: str
    source_package: str
    destination_package: str
    foreign_affinity: float
    destination_class_affinity: float
    source_state_penalty: float
    structural_score: float
    risk_level: str
    status: str
    reason: str

