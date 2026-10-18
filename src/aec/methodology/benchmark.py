"""Controlled local workflow comparison with retained SQLite evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal, cast
from uuid import uuid4

from aec.communication.broker import Broker
from aec.execution.base import ExecutionAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.execution.policy import ExecutionPolicy
from aec.security.identities import Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.loader import definition_hash
from aec.workflows.models import StepStatus, WorkflowDefinition, WorkflowStatus

from .contracts import BenchmarkRun, BenchmarkTask, MethodologyVersion

REFERENCE_TASKS = (
    BenchmarkTask(
        task_id="valid-types",
        version="1.0.0",
        instructions="Implement a typed function with valid input and output types.",
        acceptance_criteria="QA may review only after required gates pass.",
        expected_outcome="qa_pass",
        type_check_passes=True,
    ),
    BenchmarkTask(
        task_id="invalid-types",
        version="1.0.0",
        instructions="Implement a function with an incompatible return type.",
        acceptance_criteria="Reject a type error before invoking QA.",
        expected_outcome="type_rejected_before_qa",
        type_check_passes=False,
    ),
)


class ScriptedAdapter(ExecutionAdapter):
    def __init__(self, task: BenchmarkTask) -> None:
        self.task = task

    async def healthcheck(self) -> bool:
        return True

    async def cancel(self, execution_id: str) -> bool:
        return False

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        if request.agent_id == "qa":
            output = json.dumps(
                {
                    "task_id": self.task.task_id,
                    "iteration": 1,
                    "reviewed_commit": "a" * 40,
                    "review_status": "PASSED",
                    "findings": [],
                }
            )
        else:
            output = self.task.instructions
        moment = datetime.now(timezone.utc)
        return ExecutionResult(
            execution_id=request.execution_id,
            agent_id=request.agent_id,
            status=ExecutionStatus.SUCCEEDED,
            output=output,
            started_at=moment,
            finished_at=moment,
        )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _task_definition(
    version: MethodologyVersion, task: BenchmarkTask
) -> WorkflowDefinition:
    steps = []
    for step in version.definition.steps:
        if step.type == "agent" and step.agent == "implementer":
            step = step.model_copy(
                update={"inputs": {**step.inputs, "prompt": task.instructions}}
            )
        steps.append(step)
    return version.definition.model_copy(update={"steps": steps})


class LocalBenchmarkRunner:
    def __init__(
        self,
        root: Path,
        *,
        repetitions: int = 1,
        tasks: tuple[BenchmarkTask, ...] = REFERENCE_TASKS,
    ) -> None:
        if (
            repetitions < 1
            or len(tasks) < 2
            or len({t.task_id for t in tasks}) != len(tasks)
        ):
            raise ValueError("at least two distinct tasks and one repetition required")
        self.root = root
        self.repetitions = repetitions
        self.tasks = tasks

    async def compare(
        self, baseline: MethodologyVersion, candidate: MethodologyVersion
    ) -> tuple[BenchmarkRun, ...]:
        if baseline.methodology_id != candidate.methodology_id:
            raise ValueError("methodologies differ")
        batch = self.root / f"benchmark-{uuid4().hex}"
        batch.mkdir(parents=True)
        runs = []
        for task in self.tasks:
            for repetition in range(1, self.repetitions + 1):
                arms = (
                    (("baseline", baseline), ("candidate", candidate))
                    if repetition % 2
                    else (("candidate", candidate), ("baseline", baseline))
                )
                for arm, version in arms:
                    runs.append(
                        await self._run(
                            batch / task.task_id / str(repetition) / arm,
                            version,
                            task,
                            repetition,
                            arm,
                        )
                    )
        return tuple(runs)

    async def _run(
        self,
        directory: Path,
        version: MethodologyVersion,
        task: BenchmarkTask,
        repetition: int,
        arm: str,
    ) -> BenchmarkRun:
        directory.mkdir(parents=True)
        workspace = directory / "workspace"
        workspace.mkdir()
        database = directory / "aec.sqlite"
        definition = _task_definition(version, task)
        if {step.agent for step in definition.steps if step.type == "agent"} != {
            "implementer",
            "qa",
        } or {
            step.validator for step in definition.steps if step.type == "validation"
        } - {"type_check"}:
            raise ValueError("benchmark supports implementer, QA and type_check only")
        conditions = json.dumps(
            {
                "adapter": "scripted-v1",
                "task_version": task.version,
                "type_check_passes": task.type_check_passes,
                "limits": definition.limits.model_dump(),
            },
            sort_keys=True,
        )
        broker = Broker(database)
        orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
        implementer = broker.register("implementer", Role.IMPLEMENTER)
        qa = broker.register("qa", Role.QA)
        gate = broker.register("quality-gate", Role.QUALITY_GATE)
        adapter = ScriptedAdapter(task)
        executors: dict[str, ExecutionAdapter] = {
            step.executor: adapter
            for step in definition.steps
            if step.type == "agent" and step.executor is not None
        }
        manager = ExecutionManager(
            broker,
            executors,
            [workspace],
            policy=ExecutionPolicy((workspace,)),
        )
        engine = WorkflowEngine(
            broker,
            manager,
            orchestrator,
            {"implementer": implementer, "qa": qa},
            gate,
            workspace,
            {"type_check": lambda _: task.type_check_passes},
        )
        run_id = f"benchmark-{uuid4().hex}"
        status = "ERROR"
        failure = None
        started = perf_counter()
        try:
            engine.create(definition, run_id)
            status = (await engine.run(run_id)).status.value
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        duration = perf_counter() - started
        steps = engine.repository.steps(run_id) if failure is None else {}
        qa_step = steps.get("qa")
        gate_step = steps.get("type_check")
        gate_passed = (
            gate_step.result.get("passed")
            if gate_step is not None and gate_step.result is not None
            else None
        )
        if not isinstance(gate_passed, bool):
            gate_passed = None
        qa_passed = False
        if qa_step is not None and qa_step.status == StepStatus.SUCCEEDED:
            message = broker.get_message(f"execution:{qa_step.execution_id}")
            qa_passed = (
                message is not None
                and message.type.value == "VALIDATION_RESULT"
                and getattr(message.payload, "passed", False) is True
            )
        if task.expected_outcome == "qa_pass":
            accepted = (
                status == WorkflowStatus.WAITING_APPROVAL.value
                and qa_passed
                and gate_passed is not False
            )
        else:
            accepted = (
                gate_passed is False
                and qa_step is not None
                and qa_step.status == StepStatus.SKIPPED
            )
        with broker.db.connect() as db:
            invocations = db.execute(
                "SELECT COUNT(*) FROM executions WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        return BenchmarkRun(
            task_id=task.task_id,
            repetition=repetition,
            arm=cast(Literal["baseline", "candidate"], arm),
            run_id=run_id,
            methodology_version=version.version,
            definition_sha256=definition_hash(version.definition),
            executed_definition_sha256=definition_hash(definition),
            acceptance_sha256=_hash(task.acceptance_criteria),
            conditions_sha256=_hash(conditions),
            workflow_status=status,
            accepted=accepted,
            gate_passed=gate_passed,
            review_iterations=None,
            duration_seconds=duration,
            agent_invocations=invocations,
            database_path=str(database),
            workspace_path=str(workspace),
            failure=failure,
        )
