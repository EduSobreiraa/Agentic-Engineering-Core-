"""One supervised read-only Codex turn with an explicit model and local policy."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from aec.communication.broker import Broker
from aec.communication.schemas import Message, MessageType, TaskAssigned
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import CodexSettings, ExecutionBudget, ExecutionRequest
from aec.execution.policy import ExecutionPolicy
from aec.security.identities import Role


async def probe(workspace: Path, model: str) -> dict[str, object]:
    workspace.mkdir(parents=True, exist_ok=False)
    broker = Broker(workspace / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    agent = broker.register("implementer", Role.IMPLEMENTER)
    prompt = "Reply with exactly AEC_OPS_OK. Do not use tools."
    assignment = broker.publish(
        Message(
            protocol="ACP", version="0.1", message_id="ops-model-assignment",
            run_id="ops-model-run", task_id="ops-model-task",
            sender=orchestrator.agent_id, recipient=agent.agent_id,
            type=MessageType.TASK_ASSIGNED, iteration=0,
            created_at=datetime.now(timezone.utc),
            payload=TaskAssigned(title="Model probe", instructions=prompt),
        ), orchestrator,
    )
    policy = ExecutionPolicy((workspace,))
    manager = ExecutionManager(
        broker, {"codex": CodexCliAdapter(policy=policy)}, [workspace],
        policy=policy, max_concurrency=1,
        budget=ExecutionBudget(
            version="2", max_concurrent_agents=1, max_invocations_per_run=1,
            max_retries=0, execution_timeout_seconds=120,
            max_duration_seconds=180, max_iterations=1,
        ),
        model_by_agent={agent.agent_id: CodexSettings(
            model=model, reasoning_effort="medium"
        )},
    )
    result = await manager.execute(
        ExecutionRequest(
            execution_id="ops-model-execution", run_id="ops-model-run",
            task_id="ops-model-task", agent_id=agent.agent_id,
            executor="codex", prompt=prompt, working_directory=workspace,
            timeout_seconds=120,
        ), agent, assignment.message_id,
    )
    with broker.db.connect() as db:
        settings = db.execute(
            "SELECT requested_model,reasoning_effort,effective_model "
            "FROM execution_settings WHERE execution_id=?",
            (result.execution_id,),
        ).fetchone()
    assert settings is not None
    return {
        "run_id": "ops-model-run",
        "execution_id": result.execution_id,
        "status": result.status.value,
        "gate_passed": result.output.strip() == "AEC_OPS_OK",
        "requested_model": settings["requested_model"],
        "effective_model": settings["effective_model"],
        "reasoning_effort": settings["reasoning_effort"],
        "usage": result.usage,
        "database": str(workspace / "aec.sqlite"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    report = asyncio.run(probe(workspace, args.model))
    (workspace / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "SUCCEEDED" or report["gate_passed"] is not True:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
