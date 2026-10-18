"""Operator-owned trust boundary for local project execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionPolicy:
    trusted_roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        forbidden = {
            Path("/"),
            Path("/tmp"),
            Path.home(),
        }
        private = (Path("/etc"), Path("/usr"), Path("/var"), Path.home() / ".codex")
        roots = tuple(root.resolve(strict=True) for root in self.trusted_roots)
        if not roots or any(
            root in forbidden
            or any(root == path or path in root.parents for path in private)
            for root in roots
        ):
            raise ValueError("specific trusted project roots are required")
        object.__setattr__(self, "trusted_roots", roots)

    def require(self, path: Path) -> None:
        resolved = path.resolve(strict=True)
        if not any(
            resolved == root or root in resolved.parents for root in self.trusted_roots
        ):
            raise PermissionError("project is not trusted for execution")
