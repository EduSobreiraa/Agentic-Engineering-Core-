from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aec.security.local_gateway import LocalApprovalGateway, LocalOperator


class Decisions:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def decide(
        self, proposal_id: str, actor: str, decision: str, *, candidate_sha256: str
    ) -> Any:
        self.calls.append((proposal_id, actor, decision, candidate_sha256))
        return SimpleNamespace(decision=decision, proposal_id=proposal_id)

    def publish(self, proposal_id: str, actor: str) -> Any:
        self.calls.append((proposal_id, actor, "publish"))
        return SimpleNamespace(
            methodology_id="security-audit", version="1.0.3", sha256="abc"
        )


class Workflows:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def decide(
        self,
        run_id: str,
        step_id: str,
        actor: str,
        approved: bool,
        *,
        manifest_sha256: str | None,
    ) -> Any:
        self.calls.append((run_id, step_id, actor, approved, manifest_sha256))
        return SimpleNamespace(status=SimpleNamespace(value="RUNNING"), run_id=run_id)


class Executions:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def reconcile_execution(
        self,
        execution_id: str,
        actor: str,
        evidence: str,
        *,
        effects_status: str,
        confirm_workspace_inspected: bool,
    ) -> None:
        self.calls.append(
            (
                execution_id,
                actor,
                evidence,
                effects_status,
                confirm_workspace_inspected,
            )
        )


class Skills:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def enable(self, skill_id: str, actor: str) -> None:
        self.calls.append((skill_id, actor))


def send(server: LocalApprovalGateway, request: dict[str, object]) -> dict[str, str]:
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(server.path))
        client.sendall((json.dumps(request) + "\n").encode())
        reply = client.makefile("r").readline()
    thread.join(timeout=5)
    assert not thread.is_alive()
    result: dict[str, str] = json.loads(reply)
    return result


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_kernel_uid_authorizes_actions_and_binds_actor_and_digest(
    tmp_path: Path,
) -> None:
    decisions, workflows, executions = Decisions(), Workflows(), Executions()
    operator = LocalOperator(
        "verified-local-user",
        frozenset(
            {
                "methodology.decide",
                "methodology.publish",
                "workflow.decide",
                "execution.reconcile",
            }
        ),
    )
    with LocalApprovalGateway(
        tmp_path / "approvals.sock",
        {os.getuid(): operator},
        methodology=cast(Any, decisions),
        workflows=cast(Any, workflows),
        executions=cast(Any, executions),
    ) as gateway:
        assert send(
            gateway,
            {
                "action": "methodology.decide",
                "proposal_id": "p-1",
                "decision": "APPROVED",
                "candidate_sha256": "exact-digest",
            },
        ) == {"proposal_id": "p-1", "status": "APPROVED"}
        assert (
            send(gateway, {"action": "methodology.publish", "proposal_id": "p-1"})[
                "status"
            ]
            == "PUBLISHED"
        )
        assert send(
            gateway,
            {
                "action": "workflow.decide",
                "run_id": "run-1",
                "step_id": "approval",
                "approved": True,
                "manifest_sha256": "artifact-digest",
            },
        ) == {"run_id": "run-1", "status": "RUNNING"}
        assert send(
            gateway,
            {
                "action": "execution.reconcile",
                "execution_id": "exec-1",
                "evidence": "inspected",
                "effects_status": "UNKNOWN",
                "confirm_workspace_inspected": True,
            },
        ) == {"status": "RECONCILED"}
        assert send(
            gateway,
            {
                "action": "workflow.decide",
                "run_id": "run-2",
                "step_id": "approval",
                "approved": True,
                "manifest_sha256": "digest",
                "actor": "forged",
            },
        ) == {"error": "ValueError"}
    assert decisions.calls == [
        ("p-1", "verified-local-user", "APPROVED", "exact-digest"),
        ("p-1", "verified-local-user", "publish"),
    ]
    assert workflows.calls == [
        ("run-1", "approval", "verified-local-user", True, "artifact-digest")
    ]
    assert executions.calls == [
        ("exec-1", "verified-local-user", "inspected", "UNKNOWN", True)
    ]


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_unknown_uid_or_disallowed_action_rejected(tmp_path: Path) -> None:
    decisions = Decisions()
    with LocalApprovalGateway(
        tmp_path / "approvals.sock",
        {os.getuid() + 1: LocalOperator("someone", frozenset({"methodology.publish"}))},
        methodology=cast(Any, decisions),
    ) as gateway:
        assert send(
            gateway, {"action": "methodology.publish", "proposal_id": "p-1"}
        ) == {"error": "PermissionError"}
    with LocalApprovalGateway(
        tmp_path / "approvals.sock",
        {os.getuid(): LocalOperator("operator", frozenset({"methodology.decide"}))},
        methodology=cast(Any, decisions),
    ) as gateway:
        assert send(
            gateway, {"action": "methodology.publish", "proposal_id": "p-1"}
        ) == {"error": "PermissionError"}
    assert decisions.calls == []


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_skill_enable_requires_verified_local_operator(tmp_path: Path) -> None:
    skills = Skills()
    with LocalApprovalGateway(
        tmp_path / "approvals.sock",
        {os.getuid(): LocalOperator("verified-user", frozenset({"skill.enable"}))},
        skills=cast(Any, skills),
    ) as gateway:
        assert send(gateway, {
            "action": "skill.enable", "skill_id": "pstack:unslop"
        }) == {
            "status": "ENABLED", "skill_id": "pstack:unslop"
        }
        assert send(gateway, {
            "action": "skill.enable", "skill_id": "pstack:unslop", "actor": "forged"
        }) == {"error": "ValueError"}
    assert skills.calls == [("pstack:unslop", "verified-user")]
