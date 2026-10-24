import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import Message, MessageType, TaskAssigned
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.base import (
    ExecutionAdapter,
    ExecutionLimitExceeded,
    ModelPolicyError,
)
from aec.execution.manager import ExecutionManager
from aec.execution.models import (
    CodexSettings,
    ExecutionBudget,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from aec.execution.policy import ExecutionPolicy
from aec.security.identities import Identity, Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.models import WorkflowDefinition


class RecordingAdapter(ExecutionAdapter):
    def __init__(self) -> None:
        self.requests: list[ExecutionRequest] = []

    async def healthcheck(self) -> bool:
        return True

    async def cancel(self, execution_id: str) -> bool:
        return False

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        self.requests.append(request)
        moment = datetime.now(timezone.utc)
        return ExecutionResult(
            execution_id=request.execution_id, agent_id=request.agent_id,
            status=ExecutionStatus.SUCCEEDED, output="done",
            started_at=moment, finished_at=moment,
        )


def assignment(
    broker: Broker, orchestrator: Identity, agent: Identity,
    execution_id: str, *, model: str | None = None,
) -> Message:
    return broker.publish(
        Message(
            protocol="ACP", version="0.1", message_id=execution_id,
            run_id="run-1", task_id=execution_id,
            sender=orchestrator.agent_id, recipient=agent.agent_id,
            type=MessageType.TASK_ASSIGNED, iteration=0,
            created_at=datetime.now(timezone.utc),
            payload=TaskAssigned(title=execution_id, instructions="do work"),
            metadata={"codex_model": model} if model is not None else {},
        ), orchestrator,
    )


def request(execution_id: str, path: Path) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id, run_id="run-1", task_id=execution_id,
        agent_id="implementer", executor="codex", prompt="do work",
        working_directory=path,
    )


def operational_budget() -> ExecutionBudget:
    return ExecutionBudget(
        version="2", max_concurrent_agents=1, max_invocations_per_run=2,
        max_retries=0, execution_timeout_seconds=60,
        max_duration_seconds=300, max_iterations=2,
    )


def test_versioned_policy_rejects_incomplete_limits() -> None:
    with pytest.raises(ValueError):
        ExecutionBudget(version="2", max_invocations_per_run=2)
    with pytest.raises(ValueError):
        ExecutionBudget(max_invocations_per_run=2, max_retries=1)


def test_policy_blocks_timeout_and_expired_run_before_adapter(tmp_path: Path) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    agent = broker.register("implementer", Role.IMPLEMENTER)
    adapter = RecordingAdapter()
    manager = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path],
        policy=ExecutionPolicy((tmp_path,)), max_concurrency=1,
        budget=operational_budget(),
    )
    message = assignment(broker, orchestrator, agent, "first")
    with pytest.raises(ExecutionLimitExceeded, match="execution_timeout_seconds"):
        asyncio.run(manager.execute(
            request("first", tmp_path), agent, message.message_id
        ))
    assert manager.repository.get("first") is None
    allowed = request("first", tmp_path)
    allowed.timeout_seconds = 30
    with broker.db.connect() as db:
        db.execute(
            "INSERT INTO workflow_runs VALUES (?,?,?,?,?,?,?,?)",
            ("run-1", "{}", "a" * 64, "RUNNING",
             "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00",
             None, None),
        )
    with pytest.raises(ExecutionLimitExceeded, match="max_duration_seconds"):
        asyncio.run(manager.execute(allowed, agent, message.message_id))
    assert adapter.requests == []
    with broker.db.connect() as db:
        reasons = {
            row[0] for row in db.execute(
                "SELECT detail FROM events WHERE kind='EXECUTION_BLOCKED'"
            )
        }
    assert reasons == {"execution_timeout_seconds", "max_duration_seconds"}


def test_workflow_policy_rejects_retry_and_duration_above_limits(
    tmp_path: Path,
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    agent = broker.register("implementer", Role.IMPLEMENTER)
    manager = ExecutionManager(
        broker, {"codex": RecordingAdapter()}, [tmp_path],
        policy=ExecutionPolicy((tmp_path,)), max_concurrency=1,
        budget=operational_budget(),
    )
    engine = WorkflowEngine(
        broker, manager, orchestrator, {"implementer": agent}, gate,
        tmp_path, {"check": lambda _: True},
    )
    definition = WorkflowDefinition.model_validate({
        "id": "policy", "version": "1.0.0", "name": "policy",
        "limits": {"max_duration_seconds": 301},
        "steps": [{
            "id": "check", "type": "validation", "validator": "check",
            "retry_policy": {"max_attempts": 2},
            "timeout_seconds": 60,
        }],
    })
    with pytest.raises(ExecutionLimitExceeded):
        engine.create(definition)
    definition.limits.max_duration_seconds = 300
    with pytest.raises(ExecutionLimitExceeded):
        engine.create(definition)
    definition.steps[0].retry_policy.max_attempts = 1
    assert engine.create(definition).status.value == "VALIDATED"


def test_concurrency_limit_applies_across_manager_instances(tmp_path: Path) -> None:
    class WaitingAdapter(RecordingAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            self.requests.append(request)
            if len(self.requests) == 1:
                self.started.set()
                await self.release.wait()
            moment = datetime.now(timezone.utc)
            return ExecutionResult(
                execution_id=request.execution_id, agent_id=request.agent_id,
                status=ExecutionStatus.SUCCEEDED, output="done",
                started_at=moment, finished_at=moment,
            )

    first_workspace, second_workspace = tmp_path / "first", tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    agent = broker.register("implementer", Role.IMPLEMENTER)
    adapter = WaitingAdapter()
    policy = ExecutionPolicy((tmp_path,))
    managers = [
        ExecutionManager(
            broker, {"codex": adapter}, [tmp_path], policy=policy,
            max_concurrency=1,
        ) for _ in range(2)
    ]
    with pytest.raises(ExecutionLimitExceeded, match="concurrency differs"):
        ExecutionManager(
            broker, {"codex": adapter}, [tmp_path], policy=policy,
            max_concurrency=2,
        )
    first = assignment(broker, orchestrator, agent, "first")
    second = assignment(broker, orchestrator, agent, "second")

    async def run() -> None:
        task_one = asyncio.create_task(managers[0].execute(
            request("first", first_workspace), agent, first.message_id
        ))
        await asyncio.wait_for(adapter.started.wait(), timeout=5)
        task_two = asyncio.create_task(managers[1].execute(
            request("second", second_workspace), agent, second.message_id
        ))
        await asyncio.sleep(0.1)
        assert len(adapter.requests) == 1
        adapter.release.set()
        results = await asyncio.wait_for(asyncio.gather(task_one, task_two), timeout=5)
        assert all(result.status == ExecutionStatus.SUCCEEDED for result in results)
        assert len(adapter.requests) == 2

    asyncio.run(run())


def test_budget_blocks_before_second_invocation_and_records_model(
    tmp_path: Path,
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    agent = broker.register("implementer", Role.IMPLEMENTER)
    adapter = RecordingAdapter()
    manager = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
        budget=ExecutionBudget(max_invocations_per_run=1),
        model_by_agent={"implementer": CodexSettings(
            model="gpt-test", reasoning_effort="high"
        )},
    )
    first = assignment(broker, orchestrator, agent, "first")
    assert asyncio.run(manager.execute(
        request("first", tmp_path), agent, first.message_id
    )).status == ExecutionStatus.SUCCEEDED
    assert adapter.requests[0].metadata == {
        "codex_model": "gpt-test", "codex_reasoning_effort": "high"
    }
    with sqlite3.connect(broker.db.path) as db:
        assert db.execute(
            "SELECT requested_model,reasoning_effort,effective_model "
            "FROM execution_settings WHERE execution_id='first'"
        ).fetchone() == ("gpt-test", "high", None)
        assert db.execute(
            "SELECT content FROM run_execution_budgets WHERE run_id='run-1'"
        ).fetchone() is not None
    second = assignment(broker, orchestrator, agent, "second")
    with pytest.raises(ExecutionLimitExceeded):
        asyncio.run(manager.execute(
            request("second", tmp_path), agent, second.message_id
        ))
    assert len(adapter.requests) == 1
    assert manager.repository.get("second") is None
    with sqlite3.connect(broker.db.path) as db:
        assert db.execute(
            "SELECT kind,detail FROM events WHERE message_id='second' "
            "AND kind='EXECUTION_BLOCKED'"
        ).fetchone() == ("EXECUTION_BLOCKED", "max_invocations_per_run")
    restarted = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    with pytest.raises(ExecutionLimitExceeded):
        asyncio.run(restarted.execute(
            request("second", tmp_path), agent, second.message_id
        ))


def test_invalid_workflow_model_override_is_rejected_before_turn(
    tmp_path: Path,
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    agent = broker.register("implementer", Role.IMPLEMENTER)
    adapter = RecordingAdapter()
    manager = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    message = assignment(broker, orchestrator, agent, "bad", model="bad model")
    with pytest.raises(ModelPolicyError):
        asyncio.run(manager.execute(
            request("bad", tmp_path), agent, message.message_id
        ))
    assert adapter.requests == []
    assert manager.repository.get("bad") is None


def test_unavailable_codex_model_does_not_fallback(tmp_path: Path) -> None:
    script = tmp_path / "codex-fixture"
    argv_path = tmp_path / "argv.json"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(argv_path)!r}).write_text(json.dumps(sys.argv))\n"
        "sys.exit(1)\n"
    )
    script.chmod(0o755)
    adapter = CodexCliAdapter(str(script), policy=ExecutionPolicy((tmp_path,)))
    execution = request("unavailable", tmp_path)
    execution.metadata = {
        "codex_model": "unavailable-model",
        "codex_reasoning_effort": "high",
    }
    result = asyncio.run(adapter.execute(execution))
    assert result.status == ExecutionStatus.FAILED
    args = json.loads(argv_path.read_text())
    assert args.count("--model") == 1
    assert args[args.index("--model") + 1] == "unavailable-model"
    assert "model_reasoning_effort=high" in args
