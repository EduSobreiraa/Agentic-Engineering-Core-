from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from aec.execution.policy import ExecutionPolicy

from .git import changed_files, commit_hash


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gate_id: str
    version: str = "1.0.0"
    validator: str
    exit_code: int | None
    status: Literal["PASSED", "FAILED", "ERROR", "SKIPPED"]
    timestamp: datetime
    evidence: str


class CommandGate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gate_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]
    argv: tuple[str, ...]
    timeout_seconds: Annotated[float, Field(gt=0)] = 120


def run_gates(
    snapshot: Path,
    reviewed_commit: str,
    commands: tuple[CommandGate, ...],
    *,
    policy: ExecutionPolicy,
) -> list[GateResult]:
    policy.require(snapshot)
    now = datetime.now(timezone.utc)
    if commit_hash(snapshot) != reviewed_commit or changed_files(snapshot):
        return [
            GateResult(
                gate_id="snapshot-integrity",
                validator="git status/rev-parse",
                exit_code=None,
                status="FAILED",
                timestamp=now,
                evidence="snapshot changed after review",
            )
        ]
    results = [
        GateResult(
            gate_id="snapshot-integrity",
            validator="git status/rev-parse",
            exit_code=0,
            status="PASSED",
            timestamp=now,
            evidence=reviewed_commit,
        )
    ]
    for gate in commands:
        if not gate.argv:
            results.append(
                GateResult(
                    gate_id=gate.gate_id,
                    validator="registered command",
                    exit_code=None,
                    status="ERROR",
                    timestamp=datetime.now(timezone.utc),
                    evidence="empty command",
                )
            )
            continue
        try:
            completed = subprocess.run(
                ("env", "PYTHONDONTWRITEBYTECODE=1", *gate.argv),
                cwd=snapshot,
                capture_output=True,
                text=True,
                timeout=gate.timeout_seconds,
                check=False,
            )
            results.append(
                GateResult(
                    gate_id=gate.gate_id,
                    validator=" ".join(gate.argv),
                    exit_code=completed.returncode,
                    status="PASSED" if completed.returncode == 0 else "FAILED",
                    timestamp=datetime.now(timezone.utc),
                    evidence=(completed.stdout + completed.stderr)[-4000:],
                )
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append(
                GateResult(
                    gate_id=gate.gate_id,
                    validator=" ".join(gate.argv),
                    exit_code=None,
                    status="ERROR",
                    timestamp=datetime.now(timezone.utc),
                    evidence=type(exc).__name__,
                )
            )
    if changed_files(snapshot) or commit_hash(snapshot) != reviewed_commit:
        results.append(
            GateResult(
                gate_id="post-gate-integrity",
                validator="git status/rev-parse",
                exit_code=None,
                status="FAILED",
                timestamp=datetime.now(timezone.utc),
                evidence="snapshot changed during validation",
            )
        )
    return results
