"""Compare two read-only Codex workflows with the same acceptance gate."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import threading
from pathlib import Path

from aec.communication.broker import Broker
from aec.communication.schemas import TaskAssigned
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult
from aec.execution.policy import ExecutionPolicy
from aec.methodologies.skills import (
    SkillRegistry,
    SkillState,
    inspect_local_skill,
)
from aec.security.identities import Role
from aec.security.local_gateway import LocalApprovalGateway, LocalOperator
from aec.workflows.engine import WorkflowEngine
from aec.workflows.models import WorkflowDefinition, WorkflowStep

PROMPT = (
    "Rewrite this sentence in plain English. Reply with one sentence only: "
    "The team leveraged a multifaceted approach to facilitate the migration."
)


class InspectingCodexAdapter(CodexCliAdapter):
    def __init__(self, policy: ExecutionPolicy, skill_text: str | None) -> None:
        super().__init__(policy=policy)
        self.skill_text = skill_text
        self.prompt_sha256: str | None = None
        self.skill_in_prompt = False

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        self.prompt_sha256 = hashlib.sha256(request.prompt.encode()).hexdigest()
        self.skill_in_prompt = (
            self.skill_text is not None and self.skill_text in request.prompt
        )
        return await super().execute(request, provider_session_id)


def enable_via_gateway(
    socket_path: Path, registry: SkillRegistry, approver: str
) -> dict[str, str]:
    with LocalApprovalGateway(
        socket_path,
        {os.getuid(): LocalOperator(approver, frozenset({"skill.enable"}))},
        skills=registry,
    ) as gateway:
        server = threading.Thread(target=gateway.handle_request)
        server.start()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(b'{"action":"skill.enable","skill_id":"pstack:unslop"}\n')
            response: dict[str, str] = json.loads(client.makefile("r").readline())
        server.join(timeout=5)
        if server.is_alive() or response.get("status") != "ENABLED":
            raise RuntimeError("local gateway did not enable the skill")
        return response


async def run_arm(
    directory: Path, registry: SkillRegistry | None, use_skill: bool
) -> dict[str, str | bool | None]:
    directory.mkdir(parents=True)
    broker = Broker(directory / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    policy = ExecutionPolicy((directory,))
    skill_text = (
        registry.references["pstack:unslop"].instruction_files[0].read_text()
        if use_skill and registry is not None else None
    )
    adapter = InspectingCodexAdapter(policy, skill_text)
    manager = ExecutionManager(
        broker, {"codex": adapter}, [directory], policy=policy
    )
    engine = WorkflowEngine(
        broker, manager, orchestrator, {"implementer": implementer},
        gate, directory,
        {"plain-English": lambda inputs: (
            "team" in inputs["text"].lower()
            and any(word in inputs["text"].lower()
                    for word in ("migration", "move"))
            and "leveraged" not in inputs["text"].lower()
        )},
        skill_registry=registry,
    )
    definition = WorkflowDefinition(
        id="phase8-skill-experiment", version="1.0.0",
        name="Plain English rewrite",
        steps=[
            WorkflowStep(
                id="rewrite", type="agent", agent="implementer",
                executor="codex", inputs={"prompt": PROMPT},
                timeout_seconds=180,
            ),
            WorkflowStep(
                id="gate", type="validation", validator="plain-English",
                depends_on=["rewrite"],
                inputs={"text": "{{rewrite.output}}"},
            ),
        ],
    )
    run_id = "with-skill" if use_skill else "baseline"
    engine.create(
        definition, run_id,
        skills={"rewrite": ("pstack:unslop",)} if use_skill else None,
    )
    run = await engine.run(run_id)
    steps = engine.repository.steps(run_id)
    output = steps["rewrite"].result or {}
    text = output.get("output", "")
    assert isinstance(text, str)
    gate_result = steps["gate"].result or {}
    assignment = next(
        message for message in broker.get_history(run_id)
        if message.type.value == "TASK_ASSIGNED"
    )
    assert isinstance(assignment.payload, TaskAssigned)
    assigned_prompt = assignment.payload.instructions
    bindings = engine.repository.skill_bindings(run_id, "rewrite")
    return {
        "run_id": run_id,
        "status": run.status.value,
        "gate_passed": gate_result.get("passed") is True,
        "output": text,
        "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "database": str(directory / "aec.sqlite"),
        "adapter_prompt_sha256": adapter.prompt_sha256,
        "skill_text_in_adapter_prompt": adapter.skill_in_prompt,
        "acp_prompt_matches_adapter": (
            hashlib.sha256(assigned_prompt.encode()).hexdigest()
            == adapter.prompt_sha256
        ),
        "bound_skill_sha256": bindings[0]["sha256"] if bindings else None,
        "bound_approver": bindings[0]["approved_by"] if bindings else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skill-file", type=Path, required=True)
    parser.add_argument("--approver", required=True)
    parser.add_argument("--single-skill", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    reference = inspect_local_skill(
        "pstack:unslop", args.skill_file, args.skill_file.parent.parent,
        compatible_executors=frozenset({"codex"}),
        required_permissions=frozenset({"read-only"}),
    )
    registry = SkillRegistry(frozenset({args.approver}))
    registry.register(reference)
    if registry.validate(
        "pstack:unslop", "codex", frozenset({"read-only"})
    ) != SkillState.VALIDATED:
        raise RuntimeError("selected skill is not compatible")
    gateway_response = enable_via_gateway(
        root / "approvals.sock", registry, args.approver
    )

    async def compare() -> list[dict[str, str | bool | None]]:
        if args.single_skill:
            return [await run_arm(root / "with-skill", registry, True)]
        return [
            await run_arm(root / "baseline", None, False),
            await run_arm(root / "with-skill", registry, True),
        ]

    results = asyncio.run(compare())
    report = {
        "skill_id": reference.identifier,
        "skill_sha256": reference.sha256,
        "approver": args.approver,
        "gateway_response": gateway_response,
        "executor": "Codex CLI",
        "sandbox": "read-only",
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "acceptance": "team and migration or move present; leveraged absent",
        "results": results,
        "conclusion": (
            "INCONCLUSIVE: one skill run validates propagation, not quality gain"
            if args.single_skill else
            "INCONCLUSIVE: one run per arm is insufficient for a gain claim"
        ),
    }
    (root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))
    if any(
        result["status"] != "COMPLETED" or result["gate_passed"] is not True
        for result in results
    ):
        raise SystemExit(1)
    if any(
        result["acp_prompt_matches_adapter"] is not True
        or (result["run_id"] == "with-skill" and (
            result["skill_text_in_adapter_prompt"] is not True
            or result["bound_skill_sha256"] != reference.sha256
            or result["bound_approver"] != args.approver
        ))
        for result in results
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
