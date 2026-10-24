"""Public local APIs exercised from independent Python processes."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path

import pytest

from aec.communication.broker import Broker, ClaimedDelivery
from aec.communication.errors import StateError
from aec.communication.schemas import Message, MessageType, TaskAssigned
from aec.execution.base import ExecutionAdapter, ExecutionLimitExceeded
from aec.execution.manager import ExecutionManager
from aec.execution.models import (
    ExecutionBudget,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from aec.execution.policy import ExecutionPolicy
from aec.methodology.benchmark import LocalBenchmarkRunner
from aec.methodology.governance import (
    MethodologyEvaluator,
    MethodologyGovernance,
    MethodologyRegistry,
    ProposalStore,
)
from aec.methodology.observation import ObservationCollector
from aec.methodology.worker import MethodologyWorker
from aec.persistence.database import Database
from aec.security.identities import Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.models import WorkflowDefinition, WorkflowStatus
from tests.scenarios.test_methodology_evolution import (
    base_version,
    history,
)


def _receive(pipe: Connection) -> object:
    assert pipe.poll(10), "child process did not respond"
    return pipe.recv()


def _join(process: BaseProcess, expected: int = 0) -> None:
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join()
    assert process.exitcode == expected


def _publish(database: str, pipe: Connection) -> None:
    broker = Broker(database)
    sender = broker.register("orchestrator", Role.ORCHESTRATOR)
    broker.register("implementer", Role.IMPLEMENTER)
    message = broker.publish(
        Message(
            protocol="ACP",
            version="0.1",
            message_id="multiprocess-assignment",
            run_id="multiprocess-run",
            task_id="task",
            sender=sender.agent_id,
            recipient="implementer",
            type=MessageType.TASK_ASSIGNED,
            iteration=0,
            created_at=datetime.now(timezone.utc),
            payload=TaskAssigned(title="task", instructions="done"),
        ),
        sender,
    )
    pipe.send(message.message_id)


def _consume(database: str, pipe: Connection, *, crash: bool = False) -> None:
    broker = Broker(database)
    worker = broker.register("implementer", Role.IMPLEMENTER)
    pipe.send("ready")
    assert pipe.recv() == "go"
    claim = broker.claim(worker.agent_id, worker)
    if claim is None:
        pipe.send(None)
        return
    pipe.send((claim.delivery_id, claim.claim_token, claim.message.message_id))
    if crash:
        os._exit(9)
    broker.ack(claim, worker)


def _recover(database: str, when: str, pipe: Connection) -> None:
    future = datetime.fromisoformat(when)
    broker = Broker(database, lease_seconds=5, clock=lambda: future)
    worker = broker.register("implementer", Role.IMPLEMENTER)
    pipe.send(broker.recover_expired_deliveries())
    claim = broker.claim(worker.agent_id, worker)
    assert claim is not None
    broker.ack(claim, worker)
    pipe.send((claim.attempts, claim.message.message_id))


def test_acp_claims_are_exclusive_across_processes(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    database = str(tmp_path / "aec.sqlite")
    publisher_parent, publisher_child = ctx.Pipe()
    publisher = ctx.Process(target=_publish, args=(database, publisher_child))
    publisher.start()
    message_id = _receive(publisher_parent)
    _join(publisher)

    pipes = [ctx.Pipe() for _ in range(2)]
    workers = [
        ctx.Process(target=_consume, args=(database, child)) for _, child in pipes
    ]
    for worker in workers:
        worker.start()
    for parent, _ in pipes:
        assert _receive(parent) == "ready"
        parent.send("go")
    claims = [_receive(parent) for parent, _ in pipes]
    for worker in workers:
        _join(worker)
    assert sum(claim is not None for claim in claims) == 1
    owned = next(claim for claim in claims if claim is not None)
    assert isinstance(owned, tuple) and owned[2] == message_id
    broker = Broker(database)
    assert [
        message.message_id for message in broker.get_history("multiprocess-run")
    ] == [message_id]
    with broker.db.connect() as db:
        receipt = db.execute(
            "SELECT COUNT(*) FROM processing_receipts WHERE message_id=?",
            (message_id,),
        ).fetchone()[0]
    assert receipt == 1


def test_acp_recovers_lease_after_process_crash(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    database = str(tmp_path / "aec.sqlite")
    parent, child = ctx.Pipe()
    publisher = ctx.Process(target=_publish, args=(database, child))
    publisher.start()
    message_id = _receive(parent)
    assert isinstance(message_id, str)
    _join(publisher)
    parent, child = ctx.Pipe()
    consumer = ctx.Process(
        target=_consume, args=(database, child), kwargs={"crash": True}
    )
    consumer.start()
    assert _receive(parent) == "ready"
    parent.send("go")
    old_claim = _receive(parent)
    _join(consumer, expected=9)
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    parent, child = ctx.Pipe()
    recovery = ctx.Process(target=_recover, args=(database, future.isoformat(), child))
    recovery.start()
    assert _receive(parent) == 1
    assert _receive(parent) == (2, message_id)
    _join(recovery)
    assert isinstance(old_claim, tuple) and old_claim[2] == message_id
    broker = Broker(database)
    old_message = broker.get_message(message_id)
    assert old_message is not None
    stale = ClaimedDelivery(old_claim[0], old_claim[1], 1, old_message)
    worker = broker.register("implementer", Role.IMPLEMENTER)
    with pytest.raises(StateError, match="stale claim token"):
        broker.ack(stale, worker)
    with broker.db.connect() as db:
        row = db.execute(
            "SELECT state,attempts FROM deliveries WHERE message_id=?", (message_id,)
        ).fetchone()
        receipts = db.execute(
            "SELECT COUNT(*) FROM processing_receipts WHERE message_id=?",
            (message_id,),
        ).fetchone()[0]
    assert (row["state"], row["attempts"], receipts) == ("ACKNOWLEDGED", 2, 1)


class DurableAdapter(ExecutionAdapter):
    def __init__(self, database: str, pipe: Connection) -> None:
        self.database = database
        self.pipe = pipe

    async def healthcheck(self) -> bool:
        return True

    async def cancel(self, execution_id: str) -> bool:
        return False

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        with sqlite3.connect(self.database) as db:
            db.execute(
                "INSERT INTO external_effects(execution_id) VALUES (?)",
                (request.execution_id,),
            )
            db.execute(
                "UPDATE concurrent_effects SET active=active+1, peak=MAX(peak,active+1)"
            )
        self.pipe.send("entered")
        command = await asyncio.to_thread(self.pipe.recv)
        if command == "crash":
            os._exit(11)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE concurrent_effects SET active=active-1")
        moment = datetime.now(timezone.utc)
        return ExecutionResult(
            execution_id=request.execution_id,
            agent_id=request.agent_id,
            status=ExecutionStatus.SUCCEEDED,
            output="done",
            started_at=moment,
            finished_at=moment,
        )


def _execute(
    database: str,
    workspace: str,
    pipe: Connection,
    execution_id: str = "one-execution",
    source_id: str = "multiprocess-assignment",
    exit_after_finish: bool = False,
    max_concurrency: int = 2,
    budget_limit: int | None = None,
    signal_ready: bool = False,
) -> None:
    broker = Broker(database)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    policy = ExecutionPolicy((Path(workspace),))
    manager = ExecutionManager(
        broker,
        {"fake": DurableAdapter(database, pipe)},
        [Path(workspace)],
        policy=policy,
        max_concurrency=max_concurrency,
        budget=(ExecutionBudget(
            version="2", max_concurrent_agents=max_concurrency,
            max_invocations_per_run=budget_limit, max_retries=0,
            execution_timeout_seconds=120, max_duration_seconds=300,
            max_iterations=1,
        ) if budget_limit is not None else None),
    )
    request = ExecutionRequest(
        execution_id=execution_id,
        run_id="multiprocess-run",
        task_id="task",
        agent_id="implementer",
        executor="fake",
        prompt="done",
        working_directory=Path(workspace),
    )
    if signal_ready:
        pipe.send("ready")
    try:
        result = asyncio.run(manager.execute(request, implementer, source_id))
    except ExecutionLimitExceeded:
        pipe.send("blocked")
        return
    pipe.send(result.status.value)
    if exit_after_finish:
        os._exit(12)


def _setup_execution(tmp_path: Path) -> tuple[str, str]:
    database = str(tmp_path / "aec.sqlite")
    broker = Broker(database)
    sender = broker.register("orchestrator", Role.ORCHESTRATOR)
    broker.register("implementer", Role.IMPLEMENTER)
    broker.publish(
        Message(
            protocol="ACP",
            version="0.1",
            message_id="multiprocess-assignment",
            run_id="multiprocess-run",
            task_id="task",
            sender=sender.agent_id,
            recipient="implementer",
            type=MessageType.TASK_ASSIGNED,
            iteration=0,
            created_at=datetime.now(timezone.utc),
            payload=TaskAssigned(title="task", instructions="done"),
        ),
        sender,
    )
    with broker.db.connect() as db:
        db.execute("CREATE TABLE external_effects(execution_id TEXT NOT NULL)")
        db.execute("CREATE TABLE concurrent_effects(active INTEGER, peak INTEGER)")
        db.execute("INSERT INTO concurrent_effects VALUES (0,0)")
    return database, str(tmp_path)


def _workflow_engine(database: str, workspace: str, pipe: Connection) -> WorkflowEngine:
    broker = Broker(database)
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    manager = ExecutionManager(
        broker,
        {"fake": DurableAdapter(database, pipe)},
        [Path(workspace)],
        policy=ExecutionPolicy((Path(workspace),)),
    )
    return WorkflowEngine(
        broker,
        manager,
        orchestrator,
        {"implementer": implementer},
        gate,
        Path(workspace),
        {},
    )


def _run_workflow(database: str, workspace: str, pipe: Connection) -> None:
    engine = _workflow_engine(database, workspace, pipe)
    try:
        result = asyncio.run(engine.run("shared-workflow"))
    except ValueError as exc:
        pipe.send(str(exc))
    else:
        pipe.send(result.status.value)


def _publish_methodology(database: str, pipe: Connection) -> None:
    db = Database(database)
    registry = MethodologyRegistry(db, validators=frozenset({"type_check"}))
    governance = MethodologyGovernance(
        ProposalStore(db), registry, local_approvers=frozenset({"human-test"})
    )
    pipe.send("ready")
    assert pipe.recv() == "go"
    publication = governance.publish("RFC-METHOD-MULTIPROCESS", "human-test")
    pipe.send((publication.version, publication.sha256))


def test_two_processes_execute_assignment_once(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    database, workspace = _setup_execution(tmp_path)
    first_parent, first_child = ctx.Pipe()
    first = ctx.Process(target=_execute, args=(database, workspace, first_child))
    first.start()
    assert _receive(first_parent) == "entered"
    second_parent, second_child = ctx.Pipe()
    second = ctx.Process(target=_execute, args=(database, workspace, second_child))
    second.start()
    first_parent.send("release")
    assert _receive(first_parent) == ExecutionStatus.SUCCEEDED.value
    assert _receive(second_parent) == ExecutionStatus.SUCCEEDED.value
    _join(first)
    _join(second)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM external_effects").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_two_processes_serialize_shared_workspace(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    database, workspace = _setup_execution(tmp_path)
    broker = Broker(database)
    sender = broker.register("orchestrator", Role.ORCHESTRATOR)
    original = broker.get_message("multiprocess-assignment")
    assert original is not None
    broker.publish(
        original.model_copy(update={"message_id": "second-assignment"}), sender
    )
    first_parent, first_child = ctx.Pipe()
    first = ctx.Process(target=_execute, args=(database, workspace, first_child))
    first.start()
    assert _receive(first_parent) == "entered"
    second_parent, second_child = ctx.Pipe()
    second = ctx.Process(
        target=_execute,
        args=(
            database,
            workspace,
            second_child,
            "second-execution",
            "second-assignment",
        ),
    )
    second.start()
    first_parent.send("release")
    assert _receive(first_parent) == ExecutionStatus.SUCCEEDED.value
    assert _receive(second_parent) == "entered"
    second_parent.send("release")
    assert _receive(second_parent) == ExecutionStatus.SUCCEEDED.value
    _join(first)
    _join(second)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM external_effects").fetchone()[0] == 2
        assert db.execute("SELECT active,peak FROM concurrent_effects").fetchone() == (
            0,
            1,
        )


def test_budget_and_concurrency_across_processes_with_distinct_workspaces(
    tmp_path: Path,
) -> None:
    ctx = multiprocessing.get_context("spawn")
    database, _ = _setup_execution(tmp_path)
    workspaces = [tmp_path / name for name in ("work-a", "work-b", "work-c")]
    for workspace in workspaces:
        workspace.mkdir()
    broker = Broker(database)
    sender = broker.register("orchestrator", Role.ORCHESTRATOR)
    original = broker.get_message("multiprocess-assignment")
    assert original is not None
    for index in (2, 3):
        broker.publish(
            original.model_copy(update={"message_id": f"assignment-{index}"}),
            sender,
        )
    processes: list[BaseProcess] = []
    parents: list[Connection] = []
    for index, workspace in enumerate(workspaces, start=1):
        parent, child = ctx.Pipe()
        source_id = (
            "multiprocess-assignment" if index == 1 else f"assignment-{index}"
        )
        worker = ctx.Process(
            target=_execute,
            args=(database, str(workspace), child, f"execution-{index}",
                  source_id, False, 1, 2, True),
        )
        processes.append(worker)
        parents.append(parent)
    processes[0].start()
    assert _receive(parents[0]) == "ready"
    assert _receive(parents[0]) == "entered"
    processes[1].start()
    assert _receive(parents[1]) == "ready"
    assert not parents[1].poll(0.2)
    parents[0].send("release")
    assert _receive(parents[0]) == ExecutionStatus.SUCCEEDED.value
    assert _receive(parents[1]) == "entered"
    parents[1].send("release")
    assert _receive(parents[1]) == ExecutionStatus.SUCCEEDED.value
    processes[2].start()
    assert _receive(parents[2]) == "ready"
    assert _receive(parents[2]) == "blocked"
    for process in processes:
        _join(process)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT active,peak FROM concurrent_effects").fetchone() == (
            0, 1,
        )
        assert db.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM external_effects").fetchone()[0] == 2


def test_two_processes_drive_workflow_once(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    database, workspace = _setup_execution(tmp_path)
    _, child = ctx.Pipe()
    engine = _workflow_engine(database, workspace, child)
    definition = WorkflowDefinition.model_validate(
        {
            "id": "shared-workflow",
            "version": "1.0.0",
            "name": "Shared workflow",
            "steps": [
                {
                    "id": "implement",
                    "type": "agent",
                    "agent": "implementer",
                    "executor": "fake",
                    "inputs": {"prompt": "done"},
                }
            ],
        }
    )
    engine.create(definition, "shared-workflow")
    first_parent, first_child = ctx.Pipe()
    first = ctx.Process(target=_run_workflow, args=(database, workspace, first_child))
    first.start()
    assert _receive(first_parent) == "entered"
    second_parent, second_child = ctx.Pipe()
    second = ctx.Process(target=_run_workflow, args=(database, workspace, second_child))
    second.start()
    first_parent.send("release")
    assert _receive(first_parent) == WorkflowStatus.COMPLETED.value
    assert _receive(second_parent) == "workflow cannot run in this state"
    _join(first)
    _join(second)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM external_effects").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM messages WHERE run_id='shared-workflow'"
            ).fetchone()[0]
            == 2
        )


def test_two_processes_publish_one_immutable_version(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    database = str(tmp_path / "aec.sqlite")
    db = Database(database)
    registry = MethodologyRegistry(db, validators=frozenset({"type_check"}))
    registry.seed(base_version(), actor="bootstrap")
    history(db, "run-021")
    history(db, "run-025")
    store = ProposalStore(db)
    _, proposal = MethodologyWorker(ObservationCollector(db), store).propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-MULTIPROCESS",
        proposed_version="1.1.0",
        expected_metric="acceptance_rate",
    )
    evaluation = asyncio.run(
        MethodologyEvaluator(store, registry).evaluate_controlled(
            proposal.proposal_id, LocalBenchmarkRunner(tmp_path / "benchmarks")
        )
    )
    assert evaluation.verdict == "PASS_WITH_WARNINGS"
    MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human-test"})
    ).decide(proposal.proposal_id, "human-test", "APPROVED")
    pipes = [ctx.Pipe() for _ in range(2)]
    publishers = [
        ctx.Process(target=_publish_methodology, args=(database, child))
        for _, child in pipes
    ]
    for publisher in publishers:
        publisher.start()
    for parent, _ in pipes:
        assert _receive(parent) == "ready"
        parent.send("go")
    results = [_receive(parent) for parent, _ in pipes]
    for publisher in publishers:
        _join(publisher)
    assert results[0] == results[1]
    assert registry.versions("dev-adversarial") == ["1.0.0", "1.1.0"]
    assert registry.active("dev-adversarial").version == "1.1.0"
    assert registry.get("dev-adversarial", "1.0.0").version == "1.0.0"
    with db.connect() as connection:
        published_events = connection.execute(
            "SELECT COUNT(*) FROM methodology_events "
            "WHERE proposal_id=? AND kind='PUBLISHED'",
            (proposal.proposal_id,),
        ).fetchone()[0]
    assert published_events == 1


def test_crashed_external_execution_is_not_repeated(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    database, workspace = _setup_execution(tmp_path)
    parent, child = ctx.Pipe()
    first = ctx.Process(target=_execute, args=(database, workspace, child))
    first.start()
    assert _receive(parent) == "entered"
    parent.send("crash")
    _join(first, expected=11)
    broker = Broker(database)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    manager = ExecutionManager(
        broker,
        {"fake": DurableAdapter(database, parent)},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    assert manager.repository.get("one-execution") == ExecutionStatus.INTERRUPTED
    request = ExecutionRequest(
        execution_id="one-execution",
        run_id="multiprocess-run",
        task_id="task",
        agent_id="implementer",
        executor="fake",
        prompt="done",
        working_directory=tmp_path,
    )
    try:
        asyncio.run(manager.execute(request, implementer, "multiprocess-assignment"))
    except RuntimeError as exc:
        assert "cannot be repeated" in str(exc)
    else:
        raise AssertionError("unknown external result was repeated")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM external_effects").fetchone()[0] == 1


def test_persisted_result_survives_process_exit(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    database, workspace = _setup_execution(tmp_path)
    parent, child = ctx.Pipe()
    first = ctx.Process(
        target=_execute,
        args=(
            database,
            workspace,
            child,
            "one-execution",
            "multiprocess-assignment",
            True,
        ),
    )
    first.start()
    assert _receive(parent) == "entered"
    parent.send("release")
    assert _receive(parent) == ExecutionStatus.SUCCEEDED.value
    _join(first, expected=12)
    parent, child = ctx.Pipe()
    second = ctx.Process(target=_execute, args=(database, workspace, child))
    second.start()
    assert _receive(parent) == ExecutionStatus.SUCCEEDED.value
    _join(second)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM external_effects").fetchone()[0] == 1
