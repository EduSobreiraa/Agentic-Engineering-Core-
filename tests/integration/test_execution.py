import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import FindingReported, MessageType, ReviewRequest
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.base import ExecutionAdapter
from aec.execution.demo import _send, run_demo
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.execution.repository import ExecutionRepository
from aec.security.identities import Role


@pytest.fixture
def fake_codex(tmp_path: Path) -> Path:
    script = tmp_path / "codex"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "if '--version' in sys.argv: print('codex-cli 0.163.0'); sys.exit(0)\n"
        "if sys.argv[1:3] == ['login', 'status']: sys.exit(0)\n"
        "prompt = sys.stdin.read()\n"
        "if 'SLEEP' in prompt: time.sleep(10)\n"
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'provider-' + str(hash(prompt))}), flush=True)\n"
        "print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':'Reply: ' + prompt}}), flush=True)\n"
        "print(json.dumps({'type':'turn.completed',"
        "'usage':{'input_tokens':3}}), flush=True)\n"
    )
    script.chmod(0o755)
    return script


def test_two_agents_exchange_over_acp(fake_codex: Path, tmp_path: Path) -> None:
    database = tmp_path / "aec.sqlite"
    history = asyncio.run(
        run_demo(database, tmp_path / "work", CodexCliAdapter(str(fake_codex)))
    )
    assert [message.type for message in history] == [
        MessageType.TASK_ASSIGNED,
        MessageType.ARTIFACT_SUBMITTED,
        MessageType.REVIEW_REQUEST,
        MessageType.FINDING_REPORTED,
    ]
    assert len({message.sender for message in history}) == 3
    assert isinstance(history[2].payload, ReviewRequest)
    assert isinstance(history[3].payload, FindingReported)
    assert history[2].payload.artifact_id == history[3].payload.artifact_id
    assert Broker(database).get_history(history[0].run_id) == history
    with Broker(database).db.connect() as db:
        rows = db.execute(
            "SELECT agent_id, working_directory, status FROM executions"
        ).fetchall()
    assert len(rows) == 2
    assert len({row["working_directory"] for row in rows}) == 2
    assert all(row["status"] == "SUCCEEDED" for row in rows)
    with Broker(database).db.connect() as db:
        sessions = db.execute(
            "SELECT session_id, agent_id FROM agent_sessions"
        ).fetchall()
    assert len(sessions) == 2
    assert len({row["session_id"] for row in sessions}) == 2
    assert {row["agent_id"] for row in sessions} == {"implementer", "qa"}


def test_adapter_timeout_and_invalid_json(fake_codex: Path, tmp_path: Path) -> None:
    adapter = CodexCliAdapter(str(fake_codex))
    request = ExecutionRequest(
        execution_id="sleep",
        run_id="run",
        task_id="task",
        agent_id="agent",
        executor="codex",
        prompt="SLEEP",
        working_directory=tmp_path,
        timeout_seconds=0.1,
    )
    result = asyncio.run(adapter.execute(request))
    assert result.status == ExecutionStatus.TIMED_OUT
    assert result.exit_code is not None
    with pytest.raises(ValueError, match="invalid Codex JSONL"):
        adapter.parse_events("not json")


def test_reconcile_and_session_ownership(fake_codex: Path, tmp_path: Path) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    repo = ExecutionRepository(broker.db)
    request = ExecutionRequest(
        execution_id="one",
        run_id="run",
        task_id="task",
        agent_id="agent",
        executor="codex",
        prompt="text",
        working_directory=tmp_path,
    )
    assert repo.create(request, "message-one")
    assert not repo.create(request, "message-one")
    repo.status("one", ExecutionStatus.RUNNING)
    assert repo.reconcile() == 1
    assert repo.get("one") == ExecutionStatus.INTERRUPTED
    with pytest.raises(ValueError, match="another request"):
        repo.create(request.model_copy(update={"prompt": "other"}), "message-one")
    with pytest.raises(ValueError, match="already has an execution"):
        repo.create(request.model_copy(update={"execution_id": "two"}), "message-one")


def test_parser_extracts_final_message() -> None:
    events = [
        {"type": "thread.started", "thread_id": "provider"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 2}},
    ]
    assert CodexCliAdapter.parse_events("\n".join(map(json.dumps, events))) == (
        "done",
        "provider",
        {"input_tokens": 2},
    )


def test_adapter_cancel(fake_codex: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CodexCliAdapter(str(fake_codex))
        request = ExecutionRequest(
            execution_id="cancel",
            run_id="run",
            task_id="task",
            agent_id="agent",
            executor="codex",
            prompt="SLEEP",
            working_directory=tmp_path,
        )
        task = asyncio.create_task(adapter.execute(request))
        while "cancel" not in adapter._running:
            await asyncio.sleep(0.01)
        assert await adapter.cancel("cancel")
        assert (await task).status == ExecutionStatus.CANCELLED

    asyncio.run(scenario())


def test_manager_serializes_same_workspace(tmp_path: Path) -> None:
    class CountingAdapter(ExecutionAdapter):
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        async def healthcheck(self) -> bool:
            return True

        async def cancel(self, execution_id: str) -> bool:
            return False

        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            now = datetime.now(timezone.utc)
            return ExecutionResult(
                execution_id=request.execution_id,
                agent_id=request.agent_id,
                status=ExecutionStatus.SUCCEEDED,
                output="done",
                exit_code=0,
                started_at=now,
                finished_at=now,
            )

    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    adapter = CountingAdapter()
    manager = ExecutionManager(broker, {"fake": adapter}, [tmp_path])
    messages = [
        _send(
            broker,
            orchestrator,
            implementer,
            MessageType.TASK_ASSIGNED,
            {"title": "task", "instructions": "done"},
            "run",
            str(index),
        )
        for index in range(2)
    ]

    async def scenario() -> None:
        results = await asyncio.gather(
            *(
                manager.execute(
                    ExecutionRequest(
                        execution_id=str(uuid4()),
                        run_id="run",
                        task_id=message.task_id,
                        agent_id="implementer",
                        executor="fake",
                        prompt="done",
                        working_directory=tmp_path,
                    ),
                    implementer,
                    message.message_id,
                )
                for message in messages
            )
        )
        assert all(result.status == ExecutionStatus.SUCCEEDED for result in results)

    asyncio.run(scenario())
    assert adapter.peak == 1
