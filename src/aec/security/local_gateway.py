"""Local operator actions over an OS-authenticated Unix socket.

The socket and SQLite file must be owned by a service account that clients cannot
write as. This gateway does not secure direct in-process API or database access.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import socketserver
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from aec.execution.manager import ExecutionManager
    from aec.methodology.governance import MethodologyGovernance
    from aec.workflows.engine import WorkflowEngine


@dataclass(frozen=True)
class LocalOperator:
    actor: str
    actions: frozenset[str]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate request key")
        result[key] = value
    return result


class LocalApprovalGateway(socketserver.UnixStreamServer):
    """One JSON request per connection; the kernel supplies the actor's UID."""

    def __init__(
        self,
        path: Path,
        operators: dict[int, LocalOperator],
        *,
        methodology: MethodologyGovernance | None = None,
        workflows: WorkflowEngine | None = None,
        executions: ExecutionManager | None = None,
    ) -> None:
        if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
            raise RuntimeError("Linux peer credentials are required")
        if not path.parent.is_dir():
            raise ValueError("socket parent directory does not exist")
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
        self.path = path
        self.operators = operators
        self.methodology = methodology
        self.workflows = workflows
        self.executions = executions
        super().__init__(str(path), _OperatorHandler)
        os.chmod(path, 0o660)
        self._inode = path.stat().st_ino

    def server_close(self) -> None:
        super().server_close()
        try:
            if self.path.stat().st_ino == self._inode:
                self.path.unlink()
        except FileNotFoundError:
            pass

    def dispatch(self, uid: int, request: dict[str, Any]) -> dict[str, str]:
        action = request.get("action")
        operator = self.operators.get(uid)
        if (
            operator is None
            or not isinstance(action, str)
            or action not in operator.actions
        ):
            raise PermissionError("operator action is not authorized")
        actor = operator.actor
        if action == "methodology.decide":
            _keys(request, {"action", "proposal_id", "decision", "candidate_sha256"})
            if self.methodology is None:
                raise ValueError("methodology governance is unavailable")
            proposal_id = _string(request, "proposal_id")
            decision = _string(request, "decision")
            digest = request.get("candidate_sha256")
            if digest is not None and (not isinstance(digest, str) or not digest):
                raise ValueError("invalid candidate digest")
            if decision not in {"APPROVED", "REJECTED", "CHANGES_REQUESTED"}:
                raise ValueError("invalid decision")
            approval = self.methodology.decide(
                proposal_id,
                actor,
                cast(Literal["APPROVED", "REJECTED", "CHANGES_REQUESTED"], decision),
                candidate_sha256=digest,
            )
            return {"status": approval.decision, "proposal_id": approval.proposal_id}
        if action == "methodology.publish":
            _keys(request, {"action", "proposal_id"})
            if self.methodology is None:
                raise ValueError("methodology governance is unavailable")
            publication = self.methodology.publish(
                _string(request, "proposal_id"), actor
            )
            return {
                "status": "PUBLISHED",
                "methodology_id": publication.methodology_id,
                "version": publication.version,
                "sha256": publication.sha256,
            }
        if action == "workflow.decide":
            _keys(
                request, {"action", "run_id", "step_id", "approved", "manifest_sha256"}
            )
            if self.workflows is None:
                raise ValueError("workflow engine is unavailable")
            approved = request.get("approved")
            if type(approved) is not bool:
                raise ValueError("approved must be a boolean")
            manifest = request.get("manifest_sha256")
            if manifest is not None and (not isinstance(manifest, str) or not manifest):
                raise ValueError("invalid manifest digest")
            run = self.workflows.decide(
                _string(request, "run_id"),
                _string(request, "step_id"),
                actor,
                approved,
                manifest_sha256=manifest,
            )
            return {"status": run.status.value, "run_id": run.run_id}
        if action == "execution.reconcile":
            _keys(
                request,
                {
                    "action",
                    "execution_id",
                    "evidence",
                    "effects_status",
                    "confirm_workspace_inspected",
                },
            )
            if self.executions is None:
                raise ValueError("execution manager is unavailable")
            effects = _string(request, "effects_status")
            if effects not in {"VERIFIED", "REVERTED", "UNKNOWN"}:
                raise ValueError("invalid effects status")
            inspected = request.get("confirm_workspace_inspected")
            if type(inspected) is not bool:
                raise ValueError("inspection confirmation must be a boolean")
            asyncio.run(
                self.executions.reconcile_execution(
                    _string(request, "execution_id"),
                    actor,
                    _string(request, "evidence"),
                    effects_status=cast(
                        Literal["VERIFIED", "REVERTED", "UNKNOWN"], effects
                    ),
                    confirm_workspace_inspected=inspected,
                )
            )
            return {"status": "RECONCILED"}
        raise ValueError("unknown action")


def _keys(request: dict[str, Any], allowed: set[str]) -> None:
    if set(request) != allowed:
        raise ValueError("request fields do not match action")


def _string(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a nonempty string")
    return value


class _OperatorHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(5)
        response: dict[str, str]
        try:
            pid, uid, gid = struct.unpack(
                "3i",
                self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
            )
            del pid, gid
            raw = self.rfile.readline(16_385)
            if not raw or len(raw) > 16_384 or not raw.endswith(b"\n"):
                raise ValueError("invalid request framing")
            request = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = self.server.dispatch(uid, request)  # type: ignore[attr-defined]
        except (PermissionError, ValueError, KeyError, RuntimeError, OSError) as exc:
            response = {"error": type(exc).__name__}
        self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode())
