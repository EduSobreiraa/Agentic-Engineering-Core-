import asyncio
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import (
    FindingReported,
    Message,
    MessageType,
    ReviewRequest,
)
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.base import ExecutionAdapter
from aec.execution.demo import _send, run_demo
from aec.execution.locking import LocalLock
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.execution.policy import ExecutionPolicy
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
        "answer = json.dumps({'severity':'critical',"
        "'description':'Missing boundary check'}) "
        "if 'Responda apenas JSON' in prompt else 'Reply: ' + prompt\n"
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'provider-' + str(hash(prompt))}), flush=True)\n"
        "print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':answer}}), flush=True)\n"
        "print(json.dumps({'type':'turn.completed',"
        "'usage':{'input_tokens':3}}), flush=True)\n"
    )
    script.chmod(0o755)
    return script


def test_resume_keeps_non_git_workspace_allowed(
    fake_codex: Path, tmp_path: Path
) -> None:
    fake_codex.write_text(
        fake_codex.read_text().replace(
            "prompt = sys.stdin.read()",
            "if 'resume' in sys.argv and '--skip-git-repo-check' "
            "not in sys.argv: sys.exit(2)\n"
            "prompt = sys.stdin.read()",
        )
    )
    adapter = CodexCliAdapter(
        str(fake_codex), policy=ExecutionPolicy((tmp_path,))
    )
    request = ExecutionRequest(
        execution_id="first",
        run_id="run",
        task_id="task",
        agent_id="implementer",
        executor="codex",
        prompt="first",
        working_directory=tmp_path,
        sandbox_mode="read-only",
    )

    async def run() -> None:
        first = await adapter.execute(request)
        assert first.status == ExecutionStatus.SUCCEEDED
        assert first.session_id is not None
        second = await adapter.execute(
            request.model_copy(update={"execution_id": "second", "prompt": "second"}),
            first.session_id,
        )
        assert second.status == ExecutionStatus.SUCCEEDED
        assert second.output == "Reply: second"

    asyncio.run(run())


def test_codex_worker_disables_python_bytecode(
    fake_codex: Path, tmp_path: Path
) -> None:
    fake_codex.write_text(
        fake_codex.read_text()
        .replace("import json, sys, time", "import json, os, sys, time")
        .replace(
            "answer = json.dumps({'severity':'critical',",
            "assert os.environ.get('PYTHONDONTWRITEBYTECODE') == '1'\n"
            "answer = json.dumps({'severity':'critical',",
        )
    )
    adapter = CodexCliAdapter(str(fake_codex), policy=ExecutionPolicy((tmp_path,)))
    result = asyncio.run(
        adapter.execute(
            ExecutionRequest(
                execution_id="bytecode",
                run_id="run",
                task_id="task",
                agent_id="implementer",
                executor="codex",
                prompt="Check worker environment",
                working_directory=tmp_path,
            )
        )
    )
    assert result.status == ExecutionStatus.SUCCEEDED


def test_two_agents_exchange_over_acp(fake_codex: Path, tmp_path: Path) -> None:
    database = tmp_path / "aec.sqlite"
    history = asyncio.run(
        run_demo(
            database,
            tmp_path / "work",
            CodexCliAdapter(str(fake_codex), policy=ExecutionPolicy((tmp_path,))),
        )
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
    assert history[3].payload.severity == "critical"
    assert Broker(database).get_history(history[0].run_id) == history
    with Broker(database).db.connect() as db:
        rows = db.execute(
            "SELECT agent_id, working_directory, status FROM executions"
        ).fetchall()
    assert len(rows) == 2
    assert len({row["working_directory"] for row in rows}) == 2
    assert all(row["status"] == "SUCCEEDED" for row in rows)
    with Broker(database).db.connect() as db:
        assert db.execute("SELECT count(*) FROM execution_processes").fetchone()[0] == 2
    with Broker(database).db.connect() as db:
        sessions = db.execute(
            "SELECT session_id, agent_id FROM agent_sessions"
        ).fetchall()
    assert len(sessions) == 2
    assert len({row["session_id"] for row in sessions}) == 2
    assert {row["agent_id"] for row in sessions} == {"implementer", "qa"}


def test_invalid_qa_output_fails_without_finding(
    fake_codex: Path, tmp_path: Path
) -> None:
    script = fake_codex.read_text().replace(
        "json.dumps({'severity':'critical','description':'Missing boundary check'})",
        "'not JSON'",
    )
    fake_codex.write_text(script)
    database = tmp_path / "aec.sqlite"
    with pytest.raises(RuntimeError, match="QA execution ended"):
        asyncio.run(
            run_demo(
                database,
                tmp_path / "work",
                CodexCliAdapter(str(fake_codex), policy=ExecutionPolicy((tmp_path,))),
            )
        )
    with Broker(database).db.connect() as db:
        rows = db.execute(
            "SELECT status, result FROM executions ORDER BY created_at"
        ).fetchall()
    assert [row["status"] for row in rows] == ["SUCCEEDED", "FAILED"]
    assert json.loads(rows[-1]["result"])["error"] == "invalid_qa_output"
    with Broker(database).db.connect() as db:
        findings = db.execute(
            "SELECT count(*) FROM messages WHERE type = 'FINDING_REPORTED'"
        ).fetchone()
    assert findings[0] == 0


def test_adapter_timeout_and_invalid_json(fake_codex: Path, tmp_path: Path) -> None:
    adapter = CodexCliAdapter(
        str(fake_codex), policy=ExecutionPolicy((tmp_path,))
    )
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
        adapter = CodexCliAdapter(
            str(fake_codex), policy=ExecutionPolicy((tmp_path,))
        )
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


def test_interrupted_execution_quarantines_workspace(
    fake_codex: Path, tmp_path: Path
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    prior = ExecutionRequest(
        execution_id="prior", run_id="prior", task_id="inspect",
        agent_id="implementer", executor="codex", prompt="prior",
        working_directory=tmp_path,
    )
    repository = ExecutionRepository(broker.db)
    assert repository.create(prior, "prior-message")
    repository.status("prior", ExecutionStatus.INTERRUPTED)
    message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "new", "instructions": "new"}, "new", "inspect",
    )
    manager = ExecutionManager(
        broker,
        {
            "codex": CodexCliAdapter(
                str(fake_codex), policy=ExecutionPolicy((tmp_path,))
            )
        },
        [tmp_path], policy=ExecutionPolicy((tmp_path,)),
        local_operators=frozenset({"human"}),
    )
    request = prior.model_copy(update={
        "execution_id": "new", "run_id": "new", "prompt": "new",
    })
    with pytest.raises(RuntimeError, match="workspace.*reconcil"):
        asyncio.run(manager.execute(request, implementer, message.message_id))
    assert repository.get("new") is None
    nested = tmp_path / "nested"
    nested.mkdir()
    nested_message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "nested", "instructions": "nested"}, "nested", "inspect",
    )
    with pytest.raises(RuntimeError, match="workspace.*reconcil"):
        asyncio.run(manager.execute(
            request.model_copy(update={
                "execution_id": "nested", "run_id": "nested",
                "prompt": "nested", "working_directory": nested,
            }),
            implementer, nested_message.message_id,
        ))
    assert repository.get("nested") is None
    with pytest.raises(PermissionError, match="operator"):
        asyncio.run(manager.reconcile_execution(
            "prior", "agent", "inspected", effects_status="UNKNOWN"
        ))
    with pytest.raises(RuntimeError, match="operator must attest"):
        asyncio.run(manager.reconcile_execution(
            "prior", "human", "inspected", effects_status="UNKNOWN"
        ))
    asyncio.run(manager.reconcile_execution(
        "prior", "human", "inspected disposable workspace and process table",
        effects_status="UNKNOWN",
        confirm_workspace_inspected=True,
    ))
    assert repository.get("prior") == ExecutionStatus.INTERRUPTED
    assert asyncio.run(
        manager.execute(request, implementer, message.message_id)
    ).status == ExecutionStatus.SUCCEEDED


def test_late_adapter_registration_records_process(
    fake_codex: Path, tmp_path: Path
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "inspect", "instructions": "inspect"}, "late", "inspect",
    )
    policy = ExecutionPolicy((tmp_path,))
    manager = ExecutionManager(
        broker, {}, [tmp_path], policy=policy,
    )
    manager.adapters["codex"] = CodexCliAdapter(str(fake_codex), policy=policy)
    result = asyncio.run(manager.execute(
        ExecutionRequest(
            execution_id="late", run_id="late", task_id="inspect",
            agent_id="implementer", executor="codex", prompt="inspect",
            working_directory=tmp_path,
        ),
        implementer, message.message_id,
    ))
    assert result.status == ExecutionStatus.SUCCEEDED
    assert manager.repository.process_info("late") is not None


def test_process_record_failure_quarantines_workspace(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "inspect", "instructions": "inspect"}, "record", "inspect",
    )
    policy = ExecutionPolicy((tmp_path,))
    manager = ExecutionManager(
        broker, {"codex": CodexCliAdapter(str(fake_codex), policy=policy)},
        [tmp_path], policy=policy,
    )

    def fail_record(_execution_id: str, _pid: int) -> None:
        raise OSError("simulated SQLite failure")

    monkeypatch.setattr(manager.repository, "record_process", fail_record)
    result = asyncio.run(manager.execute(
        ExecutionRequest(
            execution_id="record", run_id="record", task_id="inspect",
            agent_id="implementer", executor="codex", prompt="inspect",
            working_directory=tmp_path,
        ),
        implementer, message.message_id,
    ))
    assert result.status == ExecutionStatus.INTERRUPTED
    assert manager.repository.process_info("record") is None
    with pytest.raises(RuntimeError, match="workspace.*reconcil"):
        manager.repository.require_workspace_available(tmp_path)


@pytest.mark.skipif(sys.platform != "linux", reason="requires process groups")
def test_background_child_keeps_completed_turn_quarantined(
    fake_codex: Path, tmp_path: Path
) -> None:
    fake_codex.write_text(
        fake_codex.read_text().replace(
            "answer = json.dumps({'severity':'critical',",
            "if prompt == 'BACKGROUND':\n"
            "    import os, subprocess\n"
            "    child = subprocess.Popen([sys.executable, '-c',"
            " 'import time; time.sleep(5)'],"
            " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
            " stderr=subprocess.DEVNULL)\n"
            "    open('child.pid', 'w').write(str(child.pid))\n"
            "answer = json.dumps({'severity':'critical',",
        )
    )
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "background", "instructions": "BACKGROUND"},
        "background", "inspect",
    )
    policy = ExecutionPolicy((tmp_path,))
    manager = ExecutionManager(
        broker, {"codex": CodexCliAdapter(str(fake_codex), policy=policy)},
        [tmp_path], policy=policy,
    )
    child_pid: int | None = None
    try:
        result = asyncio.run(manager.execute(
            ExecutionRequest(
                execution_id="background", run_id="background", task_id="inspect",
                agent_id="implementer", executor="codex", prompt="BACKGROUND",
                working_directory=tmp_path,
            ),
            implementer, message.message_id,
        ))
        child_pid = int((tmp_path / "child.pid").read_text())
        assert result.status == ExecutionStatus.INTERRUPTED
        next_message = _send(
            broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
            {"title": "next", "instructions": "next"}, "next", "inspect",
        )
        with pytest.raises(RuntimeError, match="workspace.*reconcil"):
            asyncio.run(manager.execute(
                ExecutionRequest(
                    execution_id="next", run_id="next", task_id="inspect",
                    agent_id="implementer", executor="codex", prompt="next",
                    working_directory=tmp_path,
                ),
                implementer, next_message.message_id,
            ))
        assert manager.repository.get("next") is None
    finally:
        if child_pid is not None:
            os.kill(child_pid, signal.SIGTERM)


def test_operator_cannot_reconcile_a_live_process_group(
    fake_codex: Path, tmp_path: Path
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    request = ExecutionRequest(
        execution_id="live", run_id="live", task_id="inspect",
        agent_id="implementer", executor="codex", prompt="SLEEP",
        working_directory=tmp_path,
    )
    repository = ExecutionRepository(broker.db)
    assert repository.create(request, "live-message")
    adapter = CodexCliAdapter(str(fake_codex), policy=ExecutionPolicy((tmp_path,)))
    manager = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
        local_operators=frozenset({"human"}),
    )
    repository.status("live", ExecutionStatus.RUNNING)
    request._process_observer = repository.record_process

    async def scenario() -> None:
        task = asyncio.create_task(adapter.execute(request))
        try:
            for _ in range(100):
                if repository.process_info("live") is not None:
                    break
                await asyncio.sleep(0.01)
            info = repository.process_info("live")
            assert info is not None and info["pid"] == info["pgid"]
            repository.status("live", ExecutionStatus.INTERRUPTED)
            with pytest.raises(RuntimeError, match="still present"):
                await manager.reconcile_execution(
                    "live", "human", "process group inspected",
                    effects_status="UNKNOWN",
                    confirm_workspace_inspected=True,
                )
        finally:
            await adapter.cancel("live")
            await task
        with pytest.raises(RuntimeError, match="operator must attest"):
            await manager.reconcile_execution(
                "live", "human", "verified test process group exited",
                effects_status="VERIFIED",
            )
        await manager.reconcile_execution(
            "live", "human", "verified test process group exited",
            effects_status="VERIFIED",
            confirm_workspace_inspected=True,
        )

    asyncio.run(scenario())
    assert repository.get("live") == ExecutionStatus.INTERRUPTED
    with broker.db.connect() as db:
        row = db.execute(
            "SELECT actor,effects_status FROM execution_reconciliations "
            "WHERE execution_id='live'"
        ).fetchone()
        assert tuple(row) == ("human", "VERIFIED")


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("systemd-run") is None,
    reason="requires Linux systemd user manager",
)
def test_systemd_scope_tracks_child_outside_process_group(
    fake_codex: Path, tmp_path: Path
) -> None:
    probe = subprocess.run(
        ["systemctl", "--user", "show", "basic.target", "-p", "LoadState"],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        pytest.skip("systemd user manager unavailable")
    fake_codex.write_text(
        fake_codex.read_text().replace(
            "import json, sys, time",
            "import json, sys, time, subprocess\n"
            "from pathlib import Path\n",
        ).replace(
            "prompt = sys.stdin.read()",
            "prompt = sys.stdin.read()\n"
            "if prompt == 'DETACH':\n"
            "    child = subprocess.Popen(['sleep', '20'], "
            "start_new_session=True, stdin=subprocess.DEVNULL, "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "    Path('child.pid').write_text(str(child.pid))\n",
        )
    )
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "detached child", "instructions": "DETACH"},
        "detached", "inspect",
    )
    adapter = CodexCliAdapter(
        str(fake_codex), policy=ExecutionPolicy((tmp_path,)),
        supervise_with_systemd=True,
    )
    manager = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
        local_operators=frozenset({"human"}),
    )
    request = ExecutionRequest(
        execution_id="detached", run_id="detached", task_id="inspect",
        agent_id="implementer", executor="codex", prompt="DETACH",
        working_directory=tmp_path,
    )
    unit: str | None = None
    try:
        result = asyncio.run(manager.execute(request, implementer, message.message_id))
        assert result.status == ExecutionStatus.INTERRUPTED
        scope = manager.repository.scope_info("detached")
        assert scope is not None
        unit = scope["unit_name"]
        with pytest.raises(RuntimeError, match="still present"):
            asyncio.run(manager.reconcile_execution(
                "detached", "human", "detached child remains active",
                effects_status="UNKNOWN", confirm_workspace_inspected=True,
            ))
        child_pid = int((tmp_path / "child.pid").read_text())
        process = manager.repository.process_info("detached")
        assert process is not None
        assert os.getpgid(child_pid) != process["pgid"]
    finally:
        if unit is not None:
            subprocess.run(
                ["systemctl", "--user", "stop", unit],
                capture_output=True, check=False,
            )


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("systemd-run") is None,
    reason="requires Linux systemd user manager",
)
def test_cancel_with_unstopped_scope_remains_interrupted(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = subprocess.run(
        ["systemctl", "--user", "show", "basic.target", "-p", "LoadState"],
        capture_output=True, check=False,
    )
    if probe.returncode != 0:
        pytest.skip("systemd user manager unavailable")
    fake_codex.write_text(
        fake_codex.read_text().replace(
            "import json, sys, time",
            "import json, sys, time, subprocess\nfrom pathlib import Path",
        ).replace(
            "prompt = sys.stdin.read()",
            "prompt = sys.stdin.read()\n"
            "if prompt.startswith('DETACH'):\n"
            "    child = subprocess.Popen(['sleep', '20'], "
            "start_new_session=True, stdin=subprocess.DEVNULL, "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "    Path('child.pid').write_text(str(child.pid))\n",
        )
    )
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "cancel", "instructions": "DETACH SLEEP"}, "cancel-scope", "inspect",
    )
    adapter = CodexCliAdapter(
        str(fake_codex), policy=ExecutionPolicy((tmp_path,)),
        supervise_with_systemd=True,
    )

    async def failed_stop(unit: str) -> None:
        del unit

    monkeypatch.setattr(adapter, "_stop_scope", failed_stop)
    manager = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path], policy=ExecutionPolicy((tmp_path,)),
    )
    request = ExecutionRequest(
        execution_id="cancel-scope", run_id="cancel-scope", task_id="inspect",
        agent_id="implementer", executor="codex", prompt="DETACH SLEEP",
        working_directory=tmp_path,
    )
    unit: str | None = None
    try:
        async def scenario() -> None:
            task = asyncio.create_task(
                manager.execute(request, implementer, message.message_id)
            )
            for _ in range(200):
                if (tmp_path / "child.pid").exists():
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("detached child did not start")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        scope = manager.repository.scope_info("cancel-scope")
        assert scope is not None
        unit = scope["unit_name"]
        persisted = manager.repository.get("cancel-scope")
        assert isinstance(persisted, ExecutionResult)
        assert persisted.status == ExecutionStatus.INTERRUPTED
        assert manager.repository.scope_present("cancel-scope")
        with pytest.raises(RuntimeError, match="workspace.*reconcil"):
            manager.repository.require_workspace_available(tmp_path)
    finally:
        if unit is not None:
            subprocess.run(
                ["systemctl", "--user", "stop", unit],
                capture_output=True, check=False,
            )


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux boot ID")
def test_scope_inspection_failure_blocks_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    manager = ExecutionManager(
        broker, {}, [tmp_path], policy=ExecutionPolicy((tmp_path,)),
        local_operators=frozenset({"human"}),
    )
    request = ExecutionRequest(
        execution_id="unknown-scope", run_id="unknown-scope", task_id="inspect",
        agent_id="implementer", executor="codex", prompt="inspect",
        working_directory=tmp_path,
    )
    assert manager.repository.create(request, "unknown-message")
    manager.repository.status("unknown-scope", ExecutionStatus.RUNNING)
    manager.repository.record_scope("unknown-scope", "aec-" + "a" * 64 + ".scope")
    manager.repository.status("unknown-scope", ExecutionStatus.INTERRUPTED)

    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("user manager unavailable")

    monkeypatch.setattr("aec.execution.repository.subprocess.run", unavailable)
    with pytest.raises(RuntimeError, match="cannot be inspected"):
        asyncio.run(manager.reconcile_execution(
            "unknown-scope", "human", "inspected workspace",
            effects_status="UNKNOWN", confirm_workspace_inspected=True,
        ))
    with pytest.raises(RuntimeError, match="workspace.*reconcil"):
        manager.repository.require_workspace_available(tmp_path)
    with broker.db.connect() as db:
        assert db.execute(
            "SELECT count(*) FROM execution_reconciliations"
        ).fetchone()[0] == 0


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("systemd-run") is None,
    reason="requires Linux systemd user manager",
)
def test_systemd_scope_preserves_codex_jsonl(
    fake_codex: Path, tmp_path: Path
) -> None:
    probe = subprocess.run(
        ["systemctl", "--user", "show", "basic.target", "-p", "LoadState"],
        capture_output=True, check=False,
    )
    if probe.returncode != 0:
        pytest.skip("systemd user manager unavailable")
    history = asyncio.run(run_demo(
        tmp_path / "aec.sqlite", tmp_path / "work",
        CodexCliAdapter(
            str(fake_codex), policy=ExecutionPolicy((tmp_path,)),
            supervise_with_systemd=True,
        ),
    ))
    assert history[-1].type == MessageType.FINDING_REPORTED
    with Broker(tmp_path / "aec.sqlite").db.connect() as db:
        assert db.execute("SELECT count(*) FROM execution_scopes").fetchone()[0] == 2
        assert [row[0] for row in db.execute(
            "SELECT status FROM executions ORDER BY execution_id"
        )] == ["SUCCEEDED", "SUCCEEDED"]


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("systemd-run") is None,
    reason="requires Linux systemd user manager and fork",
)
def test_systemd_scope_survives_manager_sigkill(
    fake_codex: Path, tmp_path: Path
) -> None:
    probe = subprocess.run(
        ["systemctl", "--user", "show", "basic.target", "-p", "LoadState"],
        capture_output=True, check=False,
    )
    if probe.returncode != 0:
        pytest.skip("systemd user manager unavailable")
    database = tmp_path / "aec.sqlite"
    broker = Broker(database)
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "inspect", "instructions": "SLEEP"}, "scope-crash", "inspect",
    )
    request = ExecutionRequest(
        execution_id="scope-crash", run_id="scope-crash", task_id="inspect",
        agent_id="implementer", executor="codex", prompt="SLEEP",
        working_directory=tmp_path,
    )
    unit: str | None = None
    manager_pid = os.fork()
    if manager_pid == 0:
        try:
            child_broker = Broker(database)
            child_identity = child_broker.register("implementer", Role.IMPLEMENTER)
            manager = ExecutionManager(
                child_broker,
                {"codex": CodexCliAdapter(
                    str(fake_codex), policy=ExecutionPolicy((tmp_path,)),
                    supervise_with_systemd=True,
                )},
                [tmp_path], policy=ExecutionPolicy((tmp_path,)),
            )
            asyncio.run(manager.execute(request, child_identity, message.message_id))
        finally:
            os._exit(0)
    try:
        for _ in range(200):
            scope = ExecutionRepository(broker.db).scope_info("scope-crash")
            if scope is not None and ExecutionRepository(broker.db).scope_present(
                "scope-crash"
            ):
                unit = scope["unit_name"]
                break
            time.sleep(0.01)
        else:
            raise AssertionError("execution scope never became active")
        os.kill(manager_pid, signal.SIGKILL)
        os.waitpid(manager_pid, 0)
        manager_pid = 0
        restarted = ExecutionManager(
            broker, {}, [tmp_path], policy=ExecutionPolicy((tmp_path,)),
            local_operators=frozenset({"human"}),
        )
        assert restarted.repository.get("scope-crash") == ExecutionStatus.INTERRUPTED
        assert restarted.repository.scope_present("scope-crash")
        with pytest.raises(RuntimeError, match="workspace.*reconcil"):
            restarted.repository.require_workspace_available(tmp_path)
        with pytest.raises(RuntimeError, match="still present"):
            asyncio.run(restarted.reconcile_execution(
                "scope-crash", "human", "scope remains active",
                effects_status="UNKNOWN", confirm_workspace_inspected=True,
            ))
    finally:
        if manager_pid:
            os.kill(manager_pid, signal.SIGKILL)
            os.waitpid(manager_pid, 0)
        if unit is not None:
            subprocess.run(
                ["systemctl", "--user", "stop", unit],
                capture_output=True, check=False,
            )


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc")
@pytest.mark.parametrize("stale_identity", ["boot", "start_ticks"])
def test_stale_process_identity_still_requires_operator_inspection(
    tmp_path: Path, stale_identity: str
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    request = ExecutionRequest(
        execution_id="prior", run_id="prior", task_id="inspect",
        agent_id="implementer", executor="codex", prompt="inspect",
        working_directory=tmp_path,
    )
    manager = ExecutionManager(
        broker, {}, [tmp_path], policy=ExecutionPolicy((tmp_path,)),
        local_operators=frozenset({"human"}),
    )
    assert manager.repository.create(request, "prior-message")
    manager.repository.status("prior", ExecutionStatus.INTERRUPTED)
    current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    with broker.db.connect() as db:
        db.execute(
            "INSERT INTO execution_processes VALUES (?,?,?,?,?,?)",
            (
                "prior", os.getpid(), os.getpgrp(),
                "previous-boot" if stale_identity == "boot" else current_boot,
                0, datetime.now(timezone.utc).isoformat(),
            ),
        )
    assert manager.repository.process_group_present("prior") == (
        stale_identity == "start_ticks"
    )
    with pytest.raises(RuntimeError, match="operator must attest"):
        asyncio.run(manager.reconcile_execution(
            "prior", "human", "inspected after restart",
            effects_status="UNKNOWN",
        ))
    with pytest.raises(RuntimeError, match="workspace.*reconcil"):
        manager.repository.require_workspace_available(tmp_path)
    if stale_identity == "start_ticks":
        with pytest.raises(RuntimeError, match="still present"):
            asyncio.run(manager.reconcile_execution(
                "prior", "human", "inspected process identity and workspace",
                effects_status="UNKNOWN", confirm_workspace_inspected=True,
            ))
        with broker.db.connect() as db:
            assert db.execute(
                "SELECT count(*) FROM execution_reconciliations"
            ).fetchone()[0] == 0
        return
    asyncio.run(manager.reconcile_execution(
        "prior", "human", "inspected process identity and workspace",
        effects_status="UNKNOWN", confirm_workspace_inspected=True,
    ))
    manager.repository.require_workspace_available(tmp_path)
    with broker.db.connect() as db:
        row = db.execute(
            "SELECT actor,effects_status,evidence FROM execution_reconciliations "
            "WHERE execution_id='prior'"
        ).fetchone()
        assert tuple(row) == (
            "human", "UNKNOWN", "inspected process identity and workspace"
        )


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc and fork")
def test_manager_sigkill_keeps_orphan_quarantined(
    fake_codex: Path, tmp_path: Path
) -> None:
    database = tmp_path / "aec.sqlite"
    broker = Broker(database)
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "inspect", "instructions": "SLEEP"}, "crash", "inspect",
    )
    request = ExecutionRequest(
        execution_id="crash", run_id="crash", task_id="inspect",
        agent_id="implementer", executor="codex", prompt="SLEEP",
        working_directory=tmp_path,
    )
    read_fd, write_fd = os.pipe()
    manager_pid = os.fork()
    if manager_pid == 0:
        os.close(read_fd)
        try:
            child_broker = Broker(database)
            child_implementer = child_broker.register("implementer", Role.IMPLEMENTER)
            adapter = CodexCliAdapter(
                str(fake_codex), policy=ExecutionPolicy((tmp_path,))
            )
            manager = ExecutionManager(
                child_broker, {"codex": adapter}, [tmp_path],
                policy=ExecutionPolicy((tmp_path,)),
            )

            async def run() -> None:
                task = asyncio.create_task(manager.execute(
                    request, child_implementer, message.message_id
                ))
                for _ in range(500):
                    info = manager.repository.process_info("crash")
                    if info is not None:
                        os.write(write_fd, str(info["pid"]).encode())
                        break
                    await asyncio.sleep(0.01)
                await task

            asyncio.run(run())
        except BaseException:
            os.write(write_fd, b"ERROR")
        finally:
            os._exit(0)
    os.close(write_fd)
    codex_pid: int | None = None
    try:
        assert select.select([read_fd], [], [], 8)[0]
        marker = os.read(read_fd, 100).decode()
        assert marker.isdigit(), marker
        codex_pid = int(marker)
        os.kill(manager_pid, signal.SIGKILL)
        os.waitpid(manager_pid, 0)
        manager_pid = 0
        state = Path(f"/proc/{codex_pid}/stat").read_text().rsplit(") ", 1)[1][0]
        assert state not in {"Z", "X"}
        restarted = ExecutionManager(
            broker,
            {"codex": CodexCliAdapter(
                str(fake_codex), policy=ExecutionPolicy((tmp_path,))
            )},
            [tmp_path], policy=ExecutionPolicy((tmp_path,)),
        )
        assert restarted.repository.get("crash") == ExecutionStatus.INTERRUPTED
        info = restarted.repository.process_info("crash")
        assert info is not None and info["pid"] == codex_pid
        next_message = _send(
            broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
            {"title": "next", "instructions": "next"}, "next", "inspect",
        )
        with pytest.raises(RuntimeError, match="workspace.*reconcil"):
            asyncio.run(restarted.execute(
                request.model_copy(update={
                    "execution_id": "next", "run_id": "next", "prompt": "next",
                }),
                implementer, next_message.message_id,
            ))
        assert restarted.repository.get("next") is None
    finally:
        if manager_pid:
            os.kill(manager_pid, signal.SIGKILL)
            os.waitpid(manager_pid, 0)
        if codex_pid is not None:
            try:
                os.killpg(codex_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        os.close(read_fd)


@pytest.mark.parametrize("separate_managers", [False, True])
def test_manager_serializes_same_workspace(
    tmp_path: Path, separate_managers: bool
) -> None:
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
    manager = ExecutionManager(
        broker, {"fake": adapter}, [tmp_path], policy=ExecutionPolicy((tmp_path,))
    )
    other_manager = (
        ExecutionManager(
            broker, {"fake": adapter}, [tmp_path], policy=ExecutionPolicy((tmp_path,))
        )
        if separate_managers
        else manager
    )
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
                selected.execute(
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
                for selected, message in zip(
                    (manager, other_manager), messages, strict=True
                )
            )
        )
        assert all(result.status == ExecutionStatus.SUCCEEDED for result in results)

    asyncio.run(scenario())
    assert adapter.peak == 1


def test_restarting_manager_does_not_interrupt_active_execution(tmp_path: Path) -> None:
    class PausingAdapter(ExecutionAdapter):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def healthcheck(self) -> bool:
            return True

        async def cancel(self, execution_id: str) -> bool:
            return False

        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            self.started.set()
            await self.release.wait()
            now = datetime.now(timezone.utc)
            return ExecutionResult(
                execution_id=request.execution_id,
                agent_id=request.agent_id,
                status=ExecutionStatus.SUCCEEDED,
                output="done",
                started_at=now,
                finished_at=now,
            )

    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker,
        orchestrator,
        implementer,
        MessageType.TASK_ASSIGNED,
        {"title": "task", "instructions": "done"},
        "run",
        "task",
    )
    adapter = PausingAdapter()
    manager = ExecutionManager(
        broker, {"fake": adapter}, [tmp_path], policy=ExecutionPolicy((tmp_path,))
    )
    request = ExecutionRequest(
        execution_id="active",
        run_id="run",
        task_id="task",
        agent_id="implementer",
        executor="fake",
        prompt="done",
        working_directory=tmp_path,
    )

    async def scenario() -> None:
        task = asyncio.create_task(
            manager.execute(request, implementer, message.message_id)
        )
        await adapter.started.wait()
        restarted = ExecutionManager(
            broker, {"fake": adapter}, [tmp_path], policy=ExecutionPolicy((tmp_path,))
        )
        assert restarted.repository.get("active") == ExecutionStatus.RUNNING
        adapter.release.set()
        assert (await task).status == ExecutionStatus.SUCCEEDED

    asyncio.run(scenario())


def test_execution_lock_is_visible_to_another_process(tmp_path: Path) -> None:
    database = str(tmp_path / "aec.sqlite")
    lock = LocalLock(database, "workspace:shared")
    assert lock.try_acquire()
    check = (
        "from aec.execution.locking import LocalLock; "
        f"lock=LocalLock({database!r}, 'workspace:shared'); "
        "print(lock.try_acquire()); lock.close()"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", check],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "False"
    finally:
        lock.close()
    result = subprocess.run(
        [sys.executable, "-c", check],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "True"


def test_workspace_write_requires_manager_grant(tmp_path: Path) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    message = _send(
        broker,
        orchestrator,
        implementer,
        MessageType.TASK_ASSIGNED,
        {"title": "task", "instructions": "done"},
        "run",
        "task",
    )
    request = ExecutionRequest(
        execution_id="write",
        run_id="run",
        task_id="task",
        agent_id="implementer",
        executor="codex",
        prompt="done",
        working_directory=tmp_path,
        sandbox_mode="workspace-write",
    )
    manager = ExecutionManager(
        broker,
        {"codex": CodexCliAdapter("/bin/false", policy=ExecutionPolicy((tmp_path,)))},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    with pytest.raises(PermissionError, match="workspace-write"):
        asyncio.run(manager.execute(request, implementer, message.message_id))
    with pytest.raises(PermissionError, match="prompt"):
        asyncio.run(
            manager.execute(
                request.model_copy(
                    update={"sandbox_mode": "read-only", "prompt": "other"}
                ),
                implementer,
                message.message_id,
            )
        )
    assert manager.repository.get("write") is None
    granted = ExecutionManager(
        broker,
        {"codex": CodexCliAdapter("/bin/false", policy=ExecutionPolicy((tmp_path,)))},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
        writable_agents=frozenset({"implementer"}),
    )
    with pytest.raises(PermissionError, match="workspace-write"):
        asyncio.run(granted.execute(request, implementer, message.message_id))
    permitted = broker.publish(
        Message.model_validate(
            {
                **message.model_dump(),
                "message_id": str(uuid4()),
                "metadata": {"workspace_write": True},
            }
        ),
        orchestrator,
    )
    result = asyncio.run(granted.execute(request, implementer, permitted.message_id))
    assert result.status == ExecutionStatus.FAILED
    assert granted.repository.get("write") == result


def test_untrusted_project_cannot_start_adapter(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    untrusted = tmp_path / "untrusted"
    trusted.mkdir()
    untrusted.mkdir()
    policy = ExecutionPolicy((trusted,))
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    assignment = _send(
        broker,
        orchestrator,
        implementer,
        MessageType.TASK_ASSIGNED,
        {"title": "task", "instructions": "done"},
        "run",
        "task",
    )
    claimed_trust = broker.publish(
        assignment.model_copy(
            update={"message_id": "claimed-trust", "metadata": {"trusted": True}}
        ),
        orchestrator,
    )
    request = ExecutionRequest(
        execution_id="untrusted",
        run_id="run",
        task_id="task",
        agent_id="implementer",
        executor="codex",
        prompt="done",
        working_directory=untrusted,
        metadata={"trusted": True},
    )
    adapter = CodexCliAdapter("/bin/false", policy=policy)
    manager = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path], policy=policy
    )
    with pytest.raises(PermissionError, match="not trusted"):
        asyncio.run(manager.execute(request, implementer, claimed_trust.message_id))
    with pytest.raises(PermissionError, match="not trusted"):
        asyncio.run(adapter.execute(request))
    assert manager.repository.get(request.execution_id) is None


def test_trust_policy_rejects_broad_roots_and_symlink_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="specific trusted"):
        ExecutionPolicy((Path("/"),))
    with pytest.raises(ValueError, match="specific trusted"):
        ExecutionPolicy((Path.home(),))
    trusted = tmp_path / "trusted"
    outside = tmp_path / "outside"
    trusted.mkdir()
    outside.mkdir()
    (trusted / "escape").symlink_to(outside, target_is_directory=True)
    policy = ExecutionPolicy((trusted,))
    with pytest.raises(PermissionError, match="not trusted"):
        policy.require(trusted / "escape")
