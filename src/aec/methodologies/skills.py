"""Metadata for explicitly inspected local skills; this module never executes them."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True)
class SkillReference:
    identifier: str
    origin: str
    sha256: str
    compatible_executors: frozenset[str]
    required_permissions: frozenset[str]
    instruction_files: tuple[Path, ...]
    requirements: tuple[str, ...]
    available: bool


class SkillState(StrEnum):
    DISCOVERED = "DISCOVERED"
    VALIDATED = "VALIDATED"
    INCOMPATIBLE = "INCOMPATIBLE"
    BLOCKED = "BLOCKED"
    ENABLED = "ENABLED"


class SkillUnavailableError(RuntimeError):
    """A bound skill cannot be used for an agent turn."""


class SkillRegistry:
    def __init__(self, local_approvers: frozenset[str]) -> None:
        self.local_approvers = local_approvers
        self.references: dict[str, SkillReference] = {}
        self.states: dict[str, SkillState] = {}
        self.approved_by: dict[str, str] = {}

    def register(self, reference: SkillReference) -> None:
        if reference.identifier in self.references:
            raise ValueError("skill is already registered")
        self.references[reference.identifier] = reference
        self.states[reference.identifier] = SkillState.DISCOVERED

    def validate(
        self, identifier: str, executor: str, permissions: frozenset[str]
    ) -> SkillState:
        reference = self.references[identifier]
        if executor not in reference.compatible_executors:
            state = SkillState.INCOMPATIBLE
        elif (
            not reference.available
            or reference.requirements
            or not reference.required_permissions <= permissions
            or not self._intact(reference)
        ):
            state = SkillState.BLOCKED
        else:
            state = SkillState.VALIDATED
        self.states[identifier] = state
        return state

    def enable(self, identifier: str, actor: str) -> None:
        if actor not in self.local_approvers:
            raise PermissionError("local skill approver required")
        if self.states.get(identifier) != SkillState.VALIDATED:
            raise SkillUnavailableError("skill has not been validated")
        self.states[identifier] = SkillState.ENABLED
        self.approved_by[identifier] = actor

    def resolve(
        self, identifier: str, executor: str, permissions: frozenset[str]
    ) -> SkillReference:
        reference = self.references.get(identifier)
        if (
            reference is None
            or self.states.get(identifier) != SkillState.ENABLED
            or executor not in reference.compatible_executors
            or not reference.required_permissions <= permissions
            or not self._intact(reference)
        ):
            raise SkillUnavailableError(f"skill is unavailable: {identifier}")
        return reference

    @staticmethod
    def _intact(reference: SkillReference) -> bool:
        if len(reference.instruction_files) != 1:
            return False
        path = reference.instruction_files[0]
        return (
            path.is_file()
            and not path.is_symlink()
            and hashlib.sha256(path.read_bytes()).hexdigest() == reference.sha256
        )


def inspect_local_skill(
    identifier: str,
    instruction_file: Path,
    trusted_root: Path,
    *,
    compatible_executors: frozenset[str],
    required_permissions: frozenset[str] = frozenset(),
    requirements: tuple[str, ...] = (),
) -> SkillReference:
    """Record an operator-selected file without installing or running its contents."""
    if not identifier or not compatible_executors or instruction_file.is_symlink():
        raise ValueError("skill ID, executor and regular instruction file required")
    root = trusted_root.resolve(strict=True)
    path = instruction_file.resolve(strict=True)
    if root not in path.parents or not path.is_file():
        raise PermissionError("skill file is outside the trusted root")
    return SkillReference(
        identifier=identifier,
        origin="local",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        compatible_executors=compatible_executors,
        required_permissions=required_permissions,
        instruction_files=(path,),
        requirements=requirements,
        available=True,
    )
