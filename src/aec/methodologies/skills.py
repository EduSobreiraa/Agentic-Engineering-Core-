"""Metadata for explicitly inspected local skills; this module never executes them."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
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
