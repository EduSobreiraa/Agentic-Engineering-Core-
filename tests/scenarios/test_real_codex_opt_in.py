"""A real subscription-consuming test, disabled by default."""

import asyncio
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import MessageType
from aec.engineering.gates import CommandGate
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.demo import _send, run_demo
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.execution.policy import ExecutionPolicy
from aec.methodologies.arena import arena_comparison, run_arena
from aec.methodologies.definitions import ARENA, ARENA_V101
from aec.methodology.contracts import MethodologyVersion
from aec.methodology.governance import MethodologyRegistry
from aec.security.identities import Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.models import StepStatus, WorkflowDefinition, WorkflowStatus


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="set AEC_RUN_REAL_CODEX=1 to consume two real Codex turns",
)
def test_real_two_agent_exchange(tmp_path: Path) -> None:
    history = asyncio.run(run_demo(tmp_path / "aec.sqlite", tmp_path / "work"))
    assert [message.type for message in history] == [
        MessageType.TASK_ASSIGNED,
        MessageType.ARTIFACT_SUBMITTED,
        MessageType.REVIEW_REQUEST,
        MessageType.FINDING_REPORTED,
    ]
    with Broker(tmp_path / "aec.sqlite").db.connect() as db:
        assert db.execute("SELECT count(*) FROM execution_processes").fetchone()[0] == 2


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1"
    or sys.platform != "linux"
    or shutil.which("systemd-run") is None,
    reason="opt-in real Codex turn under systemd user scope",
)
def test_real_codex_in_systemd_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    prompt = "Responda apenas com a palavra OK. Não altere arquivos."
    assignment = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "scope probe", "instructions": prompt}, "scope-real", "inspect",
    )
    policy = ExecutionPolicy((workspace,))
    manager = ExecutionManager(
        broker, {"codex": CodexCliAdapter(
            policy=policy, supervise_with_systemd=True,
        )},
        [workspace], policy=policy,
    )
    result = asyncio.run(manager.execute(
        ExecutionRequest(
            execution_id="scope-real", run_id="scope-real", task_id="inspect",
            agent_id="implementer", executor="codex", prompt=prompt,
            working_directory=workspace, timeout_seconds=60,
        ),
        implementer, assignment.message_id,
    ))
    assert result.status == ExecutionStatus.SUCCEEDED
    assert manager.repository.scope_info("scope-real") is not None
    assert not manager.repository.scope_present("scope-real")
    assert not any(workspace.iterdir())


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX_SIGKILL") != "1" or sys.platform != "linux",
    reason="opt-in Linux SIGKILL probe with one real Codex process",
)
def test_real_manager_sigkill_quarantines_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "aec.sqlite"
    workspace = tmp_path / "project"
    workspace.mkdir()
    broker = Broker(database)
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    prompt = "Descreva este diretório vazio em uma frase; não altere arquivos."
    assignment = _send(
        broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
        {"title": "inspect", "instructions": prompt}, "sigkill-real", "inspect",
    )
    request = ExecutionRequest(
        execution_id="sigkill-real", run_id="sigkill-real", task_id="inspect",
        agent_id="implementer", executor="codex", prompt=prompt,
        working_directory=workspace, timeout_seconds=30,
    )
    read_fd, write_fd = os.pipe()
    manager_pid = os.fork()
    if manager_pid == 0:
        os.close(read_fd)
        try:
            child_broker = Broker(database)
            child_identity = child_broker.register("implementer", Role.IMPLEMENTER)
            policy = ExecutionPolicy((workspace,))
            manager = ExecutionManager(
                child_broker, {"codex": CodexCliAdapter(policy=policy)},
                [workspace], policy=policy,
            )
            record = manager.repository.record_process

            def recorded(execution_id: str, pid: int) -> None:
                record(execution_id, pid)
                os.write(write_fd, str(pid).encode())
                time.sleep(30)

            monkeypatch.setattr(manager.repository, "record_process", recorded)
            asyncio.run(manager.execute(request, child_identity, assignment.message_id))
        except BaseException:
            os.write(write_fd, b"ERROR")
        finally:
            os._exit(0)

    os.close(write_fd)
    codex_pid: int | None = None
    observed_start: int | None = None
    identity: dict[str, int | str | None] | None = None
    try:
        assert select.select([read_fd], [], [], 15)[0], "Codex did not start"
        marker = os.read(read_fd, 100).decode()
        assert marker.isdigit(), marker
        codex_pid = int(marker)
        observed_start = int(
            Path(f"/proc/{codex_pid}/stat").read_text().rsplit(") ", 1)[1]
            .split()[19]
        )
        for _ in range(20):
            if Path(f"/proc/{codex_pid}/comm").read_text().strip() == "codex":
                break
            time.sleep(0.1)
        else:
            raise AssertionError("spawned process never became Codex")
        identity = ExecutionManager(
            broker, {"codex": CodexCliAdapter(policy=ExecutionPolicy((workspace,)))},
            [workspace], policy=ExecutionPolicy((workspace,)),
        ).repository.process_info("sigkill-real")
        assert identity is not None
        assert identity["pid"] == identity["pgid"] == codex_pid
        assert identity["boot_id"] == Path(
            "/proc/sys/kernel/random/boot_id"
        ).read_text().strip()
        assert isinstance(identity["start_ticks"], int)
        os.kill(manager_pid, signal.SIGKILL)
        os.waitpid(manager_pid, 0)
        manager_pid = 0
        state = Path(f"/proc/{codex_pid}/stat").read_text().rsplit(") ", 1)[1]
        assert state[0] not in {"Z", "X"}
        assert int(state.split()[19]) == identity["start_ticks"]
        restarted = ExecutionManager(
            broker, {"codex": CodexCliAdapter(policy=ExecutionPolicy((workspace,)))},
            [workspace], policy=ExecutionPolicy((workspace,)),
            local_operators=frozenset({"operator"}),
        )
        assert restarted.repository.get("sigkill-real") == ExecutionStatus.INTERRUPTED
        with pytest.raises(RuntimeError, match="workspace.*reconcil"):
            restarted.repository.require_workspace_available(workspace)
        next_assignment = _send(
            broker, orchestrator, implementer, MessageType.TASK_ASSIGNED,
            {"title": "next", "instructions": "next"}, "sigkill-next", "inspect",
        )
        with pytest.raises(RuntimeError, match="workspace.*reconcil"):
            asyncio.run(restarted.execute(
                request.model_copy(update={
                    "execution_id": "sigkill-next", "run_id": "sigkill-next",
                    "prompt": "next",
                }),
                implementer, next_assignment.message_id,
            ))
        assert restarted.repository.get("sigkill-next") is None
        with pytest.raises(RuntimeError, match="still present"):
            asyncio.run(restarted.reconcile_execution(
                "sigkill-real", "operator", "process still active",
                effects_status="UNKNOWN",
                confirm_workspace_inspected=True,
            ))
        with broker.db.connect() as db:
            assert db.execute(
                "SELECT count(*) FROM execution_reconciliations"
            ).fetchone()[0] == 0
    finally:
        if manager_pid:
            os.kill(manager_pid, signal.SIGKILL)
            os.waitpid(manager_pid, 0)
        if codex_pid is not None and observed_start is not None:
            current = Path(f"/proc/{codex_pid}/stat")
            if current.exists() and (
                int(current.read_text().rsplit(") ", 1)[1].split()[19])
                == observed_start
            ):
                try:
                    os.killpg(codex_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                for _ in range(20):
                    if not current.exists():
                        break
                    state = current.read_text().rsplit(") ", 1)[1][0]
                    if state in {"Z", "X"}:
                        break
                    time.sleep(0.1)
                else:
                    try:
                        os.killpg(codex_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        os.close(read_fd)


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="set AEC_RUN_REAL_CODEX=1 to consume two real Codex turns",
)
def test_real_session_resume_in_non_git_directory(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(policy=ExecutionPolicy((tmp_path,)))

    async def run() -> None:
        first_request = ExecutionRequest(
            execution_id="resume-first",
            run_id="resume-check",
            task_id="read-only",
            agent_id="implementer",
            executor="codex",
            prompt="Reply with one short sentence about software testing.",
            working_directory=tmp_path,
            sandbox_mode="read-only",
        )
        first = await adapter.execute(first_request)
        assert first.status == ExecutionStatus.SUCCEEDED
        assert first.session_id
        second = await adapter.execute(
            first_request.model_copy(
                update={
                    "execution_id": "resume-second",
                    "prompt": "Reply with one short sentence about code review.",
                }
            ),
            first.session_id,
        )
        assert second.status == ExecutionStatus.SUCCEEDED
        assert second.session_id == first.session_id
        assert second.output

    asyncio.run(run())


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="opt-in controlled interruption of one real Codex turn",
)
def test_real_codex_interruption_does_not_repeat_turn(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    policy = ExecutionPolicy((tmp_path,))
    adapter = CodexCliAdapter(policy=policy)
    manager = ExecutionManager(
        broker, {"codex": adapter}, [tmp_path], policy=policy
    )
    engine = WorkflowEngine(
        broker, manager, orchestrator, {"implementer": implementer},
        gate, project, {},
    )
    definition = WorkflowDefinition.model_validate(
        {
            "id": "interruption-probe",
            "version": "1.0.0",
            "name": "Read-only Codex interruption probe",
            "steps": [
                {
                    "id": "inspect",
                    "type": "agent",
                    "agent": "implementer",
                    "executor": "codex",
                    "inputs": {
                        "prompt": "Report the contents of this empty directory."
                    },
                }
            ],
        }
    )
    run = engine.create(definition, "real-interruption")

    async def interrupt() -> None:
        task = asyncio.create_task(engine.run(run.run_id))
        for _ in range(100):
            if adapter._running:
                break
            if task.done():
                raise AssertionError("Codex completed before interruption")
            await asyncio.sleep(0.05)
        assert adapter._running
        await asyncio.sleep(0.3)
        assert not task.done()
        assert all(process.returncode is None for process in adapter._running.values())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(interrupt())
    assert not adapter._running
    restarted_manager = ExecutionManager(
        broker, {"codex": CodexCliAdapter(policy=policy)},
        [tmp_path], policy=policy,
    )
    restarted = WorkflowEngine(
        broker, restarted_manager, orchestrator, {"implementer": implementer},
        gate, project, {},
    )
    assert restarted.recover(run.run_id).status == WorkflowStatus.INTERRUPTED
    assert (
        restarted.repository.steps(run.run_id)["inspect"].status
        == StepStatus.INTERRUPTED
    )
    with pytest.raises(ValueError, match="cannot run"):
        asyncio.run(restarted.run(run.run_id))
    with broker.db.connect() as db:
        count = db.execute(
            "SELECT count(*) FROM executions WHERE run_id=?", (run.run_id,)
        ).fetchone()[0]
    assert count == 1


class CorruptSecondCandidate(CodexCliAdapter):
    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        result = await super().execute(request, provider_session_id)
        if (
            request.agent_id == "implementer_b"
            and result.status == ExecutionStatus.SUCCEEDED
        ):
            (request.working_directory / "solution.txt").write_text("bad\n")
        return result


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="set AEC_RUN_REAL_CODEX=1 to consume two or three real Codex turns",
)
@pytest.mark.parametrize("corrupt_second", [False, True])
@pytest.mark.parametrize(
    "version", [ARENA, ARENA_V101], ids=["legacy", "measured"]
)
def test_real_sequential_arena(
    tmp_path: Path, corrupt_second: bool, version: MethodologyVersion
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    (project / "solution.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(project), "add", "solution.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=AEC",
            "-c",
            "user.email=aec@localhost",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    agents = {
        name: broker.register(name, Role.IMPLEMENTER)
        for name in ("implementer_a", "implementer_b")
    }
    agents["qa"] = broker.register("qa", Role.QA)
    quality_gate = broker.register("quality-gate", Role.QUALITY_GATE)
    policy = ExecutionPolicy((tmp_path,))
    manager = ExecutionManager(
        broker,
        {
            "codex": (
                CorruptSecondCandidate(policy=policy)
                if corrupt_second
                else CodexCliAdapter(policy=policy)
            )
        },
        [tmp_path],
        policy=policy,
        writable_agents=frozenset({"implementer_a", "implementer_b"}),
    )
    engine = WorkflowEngine(
        broker,
        manager,
        orchestrator,
        agents,
        quality_gate,
        project,
        {},
        local_approvers=frozenset({"supervisor"}),
    )
    registry = MethodologyRegistry(broker.db, validators=frozenset({"arena_selected"}))
    registry.seed(version, actor="trusted-bootstrap")
    result = asyncio.run(
        asyncio.wait_for(
            run_arena(
                engine,
                project,
                worktrees,
                "real-arena",
                "Edit only solution.txt. Replace its entire content with the "
                "single line good. "
                "Do not modify any other file.",
                ("solution.txt contains exactly good followed by a newline",),
                ("solution.txt",),
                (
                    CommandGate(
                        gate_id="exact-content",
                        argv=(
                            "python",
                            "-c",
                            "from pathlib import Path; "
                            "assert Path('solution.txt').read_text() == 'good\\n'",
                        ),
                    ),
                ),
                registry=registry,
                executor="codex",
                benchmark=(
                    CommandGate(
                        gate_id="resource-workload",
                        argv=(
                            sys.executable, "-c",
                            "from pathlib import Path; "
                            "assert Path('solution.txt').read_text() == 'good\\n'; "
                            "print('good')",
                        ),
                        timeout_seconds=10,
                    )
                    if version.version == "1.0.1"
                    else None
                ),
            ),
            timeout=300,
        )
    )
    assert len(result.candidates) == 2
    if corrupt_second:
        assert result.candidates[0].valid
        assert not result.candidates[1].valid
        assert result.winner == "implementer_a"
        assert result.qa_status == WorkflowStatus.COMPLETED
        assert result.approval_status == WorkflowStatus.WAITING_APPROVAL
    else:
        assert all(candidate.valid for candidate in result.candidates)
        assert result.winner is None
        assert result.approval_status == WorkflowStatus.FAILED
    assert arena_comparison(broker.db, "real-arena-approval")["winner"] == result.winner
    if version.version == "1.0.1":
        assert all(
            len(candidate.benchmark) == 5
            for candidate in result.candidates if candidate.valid
        )
    assert (project / "solution.txt").read_text() == "base\n"
