import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import ArtifactRef, TaskAssigned
from aec.execution.base import ExecutionAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.execution.policy import ExecutionPolicy
from aec.methodologies.skills import (
    SkillRegistry,
    SkillState,
    inspect_local_skill,
)
from aec.methodology.contracts import MethodologyVersion
from aec.methodology.governance import MethodologyRegistry
from aec.security.identities import Role
from aec.workflows.engine import TransientValidationError, WorkflowEngine
from aec.workflows.loader import WorkflowDefinitionError, load, validate
from aec.workflows.models import (
    StepStatus,
    WorkflowDefinition,
    WorkflowStatus,
    WorkflowStep,
    WorkflowTaskInput,
)
from aec.workflows.state_machine import check_transition


class SimulatedAdapter(ExecutionAdapter):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        self.prompts.append(request.prompt)
        moment = datetime.now(timezone.utc)
        return ExecutionResult(
            execution_id=request.execution_id,
            agent_id=request.agent_id,
            status=ExecutionStatus.SUCCEEDED,
            output="plan" if request.task_id == "analyze" else "artifact",
            started_at=moment,
            finished_at=moment,
        )

    async def cancel(self, execution_id: str) -> bool:
        return False

    async def healthcheck(self) -> bool:
        return True


def setup_engine(tmp_path: Path) -> tuple[WorkflowEngine, SimulatedAdapter]:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    agents = {
        name: broker.register(name, Role.IMPLEMENTER)
        for name in ("analyst", "implementer")
    }
    quality_gate = broker.register("quality-gate", Role.QUALITY_GATE)
    adapter = SimulatedAdapter()
    manager = ExecutionManager(
        broker, {"simulated": adapter}, [tmp_path], policy=ExecutionPolicy((tmp_path,))
    )
    engine = WorkflowEngine(
        broker,
        manager,
        orchestrator,
        agents,
        quality_gate,
        tmp_path,
        {"artifact-check": lambda inputs: inputs["artifact"] == "artifact"},
        local_approvers=frozenset({"supervisor"}),
    )
    return engine, adapter


def test_approved_skill_is_bound_and_rechecked_before_turn(tmp_path: Path) -> None:
    engine, adapter = setup_engine(tmp_path)
    root = tmp_path / "skills"
    root.mkdir()
    path = root / "SKILL.md"
    path.write_text("Use short sentences.")
    reference = inspect_local_skill(
        "short-writing", path, tmp_path,
        compatible_executors=frozenset({"simulated"}),
        required_permissions=frozenset({"read-only"}),
    )
    registry = SkillRegistry(frozenset({"supervisor"}))
    registry.register(reference)
    assert registry.validate(
        "short-writing", "simulated", frozenset({"read-only"})
    ) == SkillState.VALIDATED
    with pytest.raises(PermissionError):
        registry.enable("short-writing", "agent")
    registry.enable("short-writing", "supervisor")
    engine.skill_registry = registry
    definition = WorkflowDefinition(
        id="skill-check", version="1.0.0", name="Skill check",
        steps=[WorkflowStep(
            id="write", type="agent", agent="implementer",
            executor="simulated", inputs={"prompt": "Write a sentence."},
        )],
    )
    engine.create(definition, "skill-ok", skills={"write": ("short-writing",)})
    assert asyncio.run(engine.run("skill-ok")).status == WorkflowStatus.COMPLETED
    assert "Use short sentences." in adapter.prompts[0]
    binding = engine.repository.skill_bindings("skill-ok", "write")[0]
    assert binding["sha256"] == reference.sha256
    engine.create(definition, "skill-changed", skills={"write": ("short-writing",)})
    path.write_text("Changed after binding.")
    assert asyncio.run(engine.run("skill-changed")).status == WorkflowStatus.FAILED
    assert len(adapter.prompts) == 1


def example() -> WorkflowDefinition:
    return load(Path(__file__).parents[2] / "docs/workflows/example.yaml")


def test_create_from_published_methodology(tmp_path: Path) -> None:
    engine, _ = setup_engine(tmp_path)
    definition = example()
    registry = MethodologyRegistry(
        engine.broker.db, validators=frozenset({"artifact-check"})
    )
    registry.seed(
        MethodologyVersion(
            methodology_id=definition.id,
            version=definition.version,
            definition=definition,
            mandatory_gates=frozenset({"artifact-check"}),
        ),
        actor="bootstrap",
    )
    task = WorkflowTaskInput(
        requirements="Follow the example workflow",
        acceptance_criteria=("artifact check passes",),
    )
    run = engine.create_published(
        registry, definition.id, definition.version, task_input=task
    )
    with engine.broker.db.connect() as connection:
        binding = connection.execute(
            "SELECT version FROM methodology_run_bindings WHERE run_id=?",
            (run.run_id,),
        ).fetchone()
    assert binding["version"] == "1.0.0"
    with engine.broker.db.connect() as connection:
        connection.execute(
            "CREATE TRIGGER reject_methodology_binding "
            "BEFORE INSERT ON methodology_run_bindings "
            "BEGIN SELECT RAISE(ABORT, 'binding failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="binding failed"):
        engine.create_published(
            registry,
            definition.id,
            definition.version,
            run_id="rolled-back",
            task_input=task,
        )
    with pytest.raises(KeyError):
        engine.repository.get_run("rolled-back")
    with engine.broker.db.connect() as connection:
        connection.execute("DROP TRIGGER reject_methodology_binding")
        connection.execute(
            "CREATE TRIGGER reject_workflow_input "
            "BEFORE INSERT ON workflow_run_inputs "
            "BEGIN SELECT RAISE(ABORT, 'input failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="input failed"):
        engine.create_published(
            registry,
            definition.id,
            definition.version,
            run_id="input-rolled-back",
            task_input=task,
        )
    with pytest.raises(KeyError):
        engine.repository.get_run("input-rolled-back")


def test_published_task_input_survives_restart_and_reaches_acp(tmp_path: Path) -> None:
    engine, _ = setup_engine(tmp_path)
    definition = example()
    registry = MethodologyRegistry(
        engine.broker.db, validators=frozenset({"artifact-check"})
    )
    registry.seed(
        MethodologyVersion(
            methodology_id=definition.id,
            version=definition.version,
            definition=definition,
            mandatory_gates=frozenset({"artifact-check"}),
        ),
        actor="bootstrap",
    )
    task = WorkflowTaskInput(
        requirements="Implement integer addition",
        acceptance_criteria=("add(2, 3) == 5",),
        artifact_refs=(
            ArtifactRef(
                artifact_id="prior-spec",
                kind="specification",
                uri="artifact://prior-run/spec.txt",
                sha256="a" * 64,
                created_by="author",
            ),
        ),
    )
    run = engine.create_published(
        registry, definition.id, definition.version, "task-input-run", task_input=task
    )
    restarted, adapter = setup_engine(tmp_path)
    assert restarted.repository.task_input(run.run_id) == task
    assert restarted.repository.definition(run.run_id) == definition
    assert (
        asyncio.run(restarted.run(run.run_id)).status == WorkflowStatus.WAITING_APPROVAL
    )
    assert all("Implement integer addition" in prompt for prompt in adapter.prompts)
    assert all("add(2, 3) == 5" in prompt for prompt in adapter.prompts)
    assert "prior-spec" in adapter.prompts[0]
    assignments = [
        message
        for message in restarted.broker.get_history(run.run_id)
        if message.type.value == "TASK_ASSIGNED"
    ]
    assert isinstance(assignments[0].payload, TaskAssigned)
    assert assignments[0].payload.instructions == adapter.prompts[0]
    assert assignments[0].artifact_refs == []
    with restarted.broker.db.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE workflow_run_inputs SET content='changed' WHERE run_id=?",
                (run.run_id,),
            )
        connection.execute("DROP TRIGGER workflow_run_inputs_no_update")
        connection.execute(
            "UPDATE workflow_run_inputs SET content='changed' WHERE run_id=?",
            (run.run_id,),
        )
    with pytest.raises(RuntimeError, match="hash mismatch"):
        restarted.repository.task_input(run.run_id)


def test_complete_persisted_workflow_and_recovery(tmp_path: Path) -> None:
    engine, adapter = setup_engine(tmp_path)
    run = engine.create(example())
    assert run.status == WorkflowStatus.VALIDATED
    waiting = asyncio.run(engine.run(run.run_id))
    assert waiting.status == WorkflowStatus.WAITING_APPROVAL
    assert "Specification: define a pure Python function add" in adapter.prompts[0]
    assert "Implement this plan: plan" in adapter.prompts[1]
    assert [
        message.type.value for message in engine.broker.get_history(run.run_id)
    ] == [
        "TASK_ASSIGNED",
        "ARTIFACT_SUBMITTED",
        "TASK_ASSIGNED",
        "ARTIFACT_SUBMITTED",
        "VALIDATION_RESULT",
    ]
    assert (
        engine.repository.steps(run.run_id)["approval"].status
        == StepStatus.WAITING_APPROVAL
    )
    with pytest.raises(PermissionError):
        engine.decide(run.run_id, "approval", "agent", True)
    restarted, _ = setup_engine(tmp_path)
    assert restarted.recover(run.run_id).status == WorkflowStatus.WAITING_APPROVAL
    restarted.decide(run.run_id, "approval", "supervisor", True)
    assert asyncio.run(restarted.run(run.run_id)).status == WorkflowStatus.COMPLETED
    with pytest.raises(ValueError):
        restarted.decide(run.run_id, "approval", "supervisor", True)
    assert len(restarted.repository.events(run.run_id)) >= 15
    assert restarted.repository.definition(run.run_id) == example()


def test_second_engine_waits_for_same_workflow(tmp_path: Path) -> None:
    class HoldingAdapter(SimulatedAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            if not self.prompts:
                self.started.set()
                await self.release.wait()
            return await super().execute(request, provider_session_id)

    engine, _ = setup_engine(tmp_path)
    adapter = HoldingAdapter()
    engine.manager.adapters["simulated"] = adapter
    run = engine.create(example())

    async def scenario() -> None:
        first = asyncio.create_task(engine.run(run.run_id))
        await adapter.started.wait()
        second_engine, _ = setup_engine(tmp_path)
        second_engine.manager.adapters["simulated"] = adapter
        second = asyncio.create_task(second_engine.run(run.run_id))
        await asyncio.sleep(0.05)
        assert (
            second_engine.repository.get_run(run.run_id).status
            == WorkflowStatus.RUNNING
        )
        with pytest.raises(RuntimeError, match="another engine"):
            second_engine.cancel(run.run_id)
        adapter.release.set()
        results = await asyncio.gather(first, second)
        assert [result.status for result in results] == [
            WorkflowStatus.WAITING_APPROVAL,
            WorkflowStatus.WAITING_APPROVAL,
        ]

    asyncio.run(scenario())
    assert len(adapter.prompts) == 2


def test_rejection_and_unknown_execution(tmp_path: Path) -> None:
    engine, _ = setup_engine(tmp_path)
    run = engine.create(example())
    asyncio.run(engine.run(run.run_id))
    assert (
        engine.decide(run.run_id, "approval", "supervisor", False).status
        == WorkflowStatus.FAILED
    )
    second = engine.create(example())
    engine.repository.workflow_status(second.run_id, WorkflowStatus.RUNNING)
    engine.repository.step_status(second.run_id, "analyze", StepStatus.READY)
    engine.repository.step_status(
        second.run_id, "analyze", StepStatus.RUNNING, execution_id="unknown"
    )
    restarted, adapter = setup_engine(tmp_path)
    assert restarted.recover(second.run_id).status == WorkflowStatus.INTERRUPTED
    assert adapter.prompts == []


def test_invalid_definitions_and_transitions() -> None:
    data = example().model_dump()
    changes: list[Callable[[dict[str, Any]], None]] = [
        lambda d: d.update(version="2.0.0"),
        lambda d: d["steps"].append(d["steps"][0].copy()),
        lambda d: d["steps"][1].update(depends_on=["missing"]),
        lambda d: d["steps"][0].update(depends_on=["implement"]),
        lambda d: d["steps"][0].update(type="unknown"),
        lambda d: d["steps"][3].update(
            condition={"step": "analyze", "result_equals": True}
        ),
        lambda d: d["limits"].update(max_total_steps=2),
    ]
    for change in changes:
        import copy

        bad = copy.deepcopy(data)
        change(bad)
        with pytest.raises((WorkflowDefinitionError, ValueError)):
            validate(WorkflowDefinition.model_validate(bad))
    with pytest.raises(ValueError):
        check_transition(WorkflowStatus.CREATED, WorkflowStatus.COMPLETED)
    with pytest.raises(ValueError):
        check_transition(StepStatus.SUCCEEDED, StepStatus.RUNNING)


def test_malformed_yaml_and_skipped_approval(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("steps: [broken")
    with pytest.raises(WorkflowDefinitionError):
        load(bad)
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    agents = {
        name: broker.register(name, Role.IMPLEMENTER)
        for name in ("analyst", "implementer")
    }
    manager = ExecutionManager(
        broker,
        {"simulated": SimulatedAdapter()},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    engine = WorkflowEngine(
        broker,
        manager,
        orchestrator,
        agents,
        gate,
        tmp_path,
        {"artifact-check": lambda _: False},
        local_approvers=frozenset({"supervisor"}),
    )
    run = engine.create(example())
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.FAILED
    assert engine.repository.steps(run.run_id)["approval"].status == StepStatus.SKIPPED
    assert broker.get_message(f"workflow-failed:{run.run_id}") is not None


def test_parallel_limit_and_safe_validation_retry(tmp_path: Path) -> None:
    class ConcurrentAdapter(SimulatedAdapter):
        active = 0
        peak = 0

        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.02)
            result = await super().execute(request, provider_session_id)
            self.active -= 1
            return result

    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    agents = {
        name: broker.register(name, Role.IMPLEMENTER)
        for name in ("analyst", "implementer")
    }
    paths = {name: tmp_path / name for name in agents}
    for path in paths.values():
        path.mkdir()
    adapter = ConcurrentAdapter()
    manager = ExecutionManager(
        broker,
        {"simulated": adapter},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
        max_concurrency=2,
    )
    attempts = 0

    def flaky(_: dict[str, str]) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TransientValidationError("transient validator error")
        return True

    engine = WorkflowEngine(
        broker,
        manager,
        orchestrator,
        agents,
        gate,
        tmp_path,
        {"artifact-check": flaky},
        workspaces=paths,
    )
    definition = WorkflowDefinition.model_validate(
        {
            "id": "parallel",
            "version": "1.0.0",
            "name": "parallel",
            "limits": {"max_parallel_steps": 2},
            "steps": [
                {
                    "id": "analyze",
                    "type": "agent",
                    "agent": "analyst",
                    "executor": "simulated",
                    "inputs": {"prompt": "a"},
                },
                {
                    "id": "implement",
                    "type": "agent",
                    "agent": "implementer",
                    "executor": "simulated",
                    "inputs": {"prompt": "b"},
                },
                {
                    "id": "validate",
                    "type": "validation",
                    "validator": "artifact-check",
                    "depends_on": ["analyze", "implement"],
                    "retry_policy": {"max_attempts": 2},
                },
            ],
        }
    )
    run = engine.create(definition)
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.COMPLETED
    assert adapter.peak == 2
    assert attempts == 2
    assert engine.repository.steps(run.run_id)["validate"].attempt == 2


def test_cancel_running_agent(tmp_path: Path) -> None:
    class SlowAdapter(SimulatedAdapter):
        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            await asyncio.sleep(10)
            return await super().execute(request, provider_session_id)

    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    analyst = broker.register("analyst", Role.IMPLEMENTER)
    manager = ExecutionManager(
        broker,
        {"simulated": SlowAdapter()},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    engine = WorkflowEngine(
        broker, manager, orchestrator, {"analyst": analyst}, gate, tmp_path, {}
    )
    definition = WorkflowDefinition.model_validate(
        {
            "id": "cancel",
            "version": "1.0.0",
            "name": "cancel",
            "steps": [
                {
                    "id": "analyze",
                    "type": "agent",
                    "agent": "analyst",
                    "executor": "simulated",
                    "inputs": {"prompt": "a"},
                }
            ],
        }
    )
    run = engine.create(definition)

    async def scenario() -> None:
        running = asyncio.create_task(engine.run(run.run_id))
        await asyncio.sleep(0.02)
        assert engine.cancel(run.run_id).status == WorkflowStatus.CANCELLED
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(scenario())
    assert engine.repository.steps(run.run_id)["analyze"].status == StepStatus.CANCELLED


def test_demo_entrypoint_uses_simulated_adapter(tmp_path: Path) -> None:
    from aec.workflows.demo import run_demo

    database = tmp_path / "demo.sqlite"
    run_id, status = asyncio.run(run_demo(database, tmp_path / "work", approve=True))
    assert status == "COMPLETED"
    assert len(Broker(database).get_history(run_id)) == 5


def test_approval_deadline_and_cancelled_request(tmp_path: Path) -> None:
    engine, _ = setup_engine(tmp_path)
    run = engine.create(example())
    asyncio.run(engine.run(run.run_id))
    with engine.broker.db.connect() as db:
        db.execute(
            "UPDATE workflow_runs SET started_at=? WHERE run_id=?",
            ("2000-01-01T00:00:00+00:00", run.run_id),
        )
    with pytest.raises(ValueError):
        engine.decide(run.run_id, "approval", "supervisor", True)
    assert engine.repository.get_run(run.run_id).status == WorkflowStatus.FAILED
    second = engine.create(example())
    asyncio.run(engine.run(second.run_id))
    engine.cancel(second.run_id)
    assert engine.repository.approval_status(second.run_id, "approval") == "CANCELLED"


def test_skips_propagate_in_topological_order(tmp_path: Path) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    manager = ExecutionManager(
        broker,
        {"simulated": SimulatedAdapter()},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    engine = WorkflowEngine(
        broker,
        manager,
        orchestrator,
        {},
        gate,
        tmp_path,
        {"check": lambda _: False},
    )
    definition = WorkflowDefinition.model_validate(
        {
            "id": "skip-chain",
            "version": "1.0.0",
            "name": "skip chain",
            "steps": [
                {
                    "id": "last",
                    "type": "validation",
                    "validator": "check",
                    "depends_on": ["middle"],
                },
                {
                    "id": "middle",
                    "type": "validation",
                    "validator": "check",
                    "depends_on": ["first"],
                    "condition": {"step": "first", "result_equals": True},
                },
                {"id": "first", "type": "validation", "validator": "check"},
            ],
        }
    )
    run = engine.create(definition)
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.COMPLETED
    steps = engine.repository.steps(run.run_id)
    assert steps["middle"].status == steps["last"].status == StepStatus.SKIPPED


def test_recover_known_execution_without_repeating_it(tmp_path: Path) -> None:
    engine, adapter = setup_engine(tmp_path)
    run = engine.create(example())
    asyncio.run(engine.run(run.run_id))
    with engine.broker.db.connect() as db:
        db.execute(
            "UPDATE workflow_runs SET status='RUNNING' WHERE run_id=?",
            (run.run_id,),
        )
        db.execute(
            "UPDATE workflow_steps SET status='RUNNING' "
            "WHERE run_id=? AND step_id='analyze'",
            (run.run_id,),
        )
    restarted, new_adapter = setup_engine(tmp_path)
    assert restarted.recover(run.run_id).status == WorkflowStatus.RUNNING
    assert (
        restarted.repository.steps(run.run_id)["analyze"].status == StepStatus.SUCCEEDED
    )
    assert new_adapter.prompts == []
    assert (
        asyncio.run(restarted.run(run.run_id)).status == WorkflowStatus.WAITING_APPROVAL
    )
    assert "Specification: define a pure Python function add" in adapter.prompts[0]
    assert "Implement this plan: plan" in adapter.prompts[1]


def test_workflow_timeout_cancels_active_execution(tmp_path: Path) -> None:
    class SlowAdapter(SimulatedAdapter):
        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            await asyncio.sleep(1)
            return await super().execute(request, provider_session_id)

    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    analyst = broker.register("analyst", Role.IMPLEMENTER)
    manager = ExecutionManager(
        broker,
        {"simulated": SlowAdapter()},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    engine = WorkflowEngine(
        broker, manager, orchestrator, {"analyst": analyst}, gate, tmp_path, {}
    )
    definition = WorkflowDefinition.model_validate(
        {
            "id": "timeout",
            "version": "1.0.0",
            "name": "timeout",
            "limits": {"max_duration_seconds": 0.02},
            "steps": [
                {
                    "id": "analyze",
                    "type": "agent",
                    "agent": "analyst",
                    "executor": "simulated",
                    "inputs": {"prompt": "a"},
                }
            ],
        }
    )
    run = engine.create(definition)
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.FAILED
    assert (
        engine.repository.steps(run.run_id)["analyze"].status == StepStatus.INTERRUPTED
    )
    assert broker.get_message(f"workflow-failed:{run.run_id}") is not None


def test_permanent_validator_error_does_not_retry(tmp_path: Path) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    manager = ExecutionManager(
        broker,
        {"simulated": SimulatedAdapter()},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
    )
    attempts = 0

    def invalid(_: dict[str, str]) -> bool:
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid result")

    engine = WorkflowEngine(
        broker, manager, orchestrator, {}, gate, tmp_path, {"invalid": invalid}
    )
    definition = WorkflowDefinition.model_validate(
        {
            "id": "invalid",
            "version": "1.0.0",
            "name": "invalid",
            "steps": [
                {
                    "id": "check",
                    "type": "validation",
                    "validator": "invalid",
                    "retry_policy": {"max_attempts": 3},
                }
            ],
        }
    )
    run = engine.create(definition)
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.FAILED
    assert attempts == 1
    assert engine.repository.steps(run.run_id)["check"].attempt == 1


def test_demo_validator_rejects_non_artifacts() -> None:
    from aec.workflows.demo import validate_add_artifact

    assert validate_add_artifact(
        {"artifact": "def add(a: int, b: int) -> int:\n    return a + b\n"}
    )
    assert not validate_add_artifact({"artifact": "Please provide a specification."})
    assert not validate_add_artifact({"artifact": "def add(a, b):\n    return a - b\n"})
    assert not validate_add_artifact(
        {"artifact": "print('side effect')\ndef add(a,b):\n    return a+b\n"}
    )
    assert not validate_add_artifact(
        {"artifact": "@print\ndef add(a: int, b: int) -> int:\n    return a+b\n"}
    )
