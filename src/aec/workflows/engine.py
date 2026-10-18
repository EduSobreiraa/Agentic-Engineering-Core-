"""Deterministic scheduler over the ACP and Execution Layer."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from aec.communication.broker import Broker
from aec.communication.schemas import (
    Message,
    MessageType,
    ReviewRequest,
    TaskAssigned,
    TaskFailed,
    ValidationResult,
)
from aec.execution.locking import LocalLock, held
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.security.identities import Identity, Role

if TYPE_CHECKING:
    from aec.methodology.governance import MethodologyRegistry

from .loader import validate
from .models import (
    StepRun,
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from .repository import WorkflowRepository

Validator = Callable[[dict[str, str]], bool]


class TransientValidationError(RuntimeError):
    """A registered validator can explicitly request a safe retry."""


class WorkflowEngine:
    def __init__(
        self,
        broker: Broker,
        manager: ExecutionManager,
        orchestrator: Identity,
        agents: dict[str, Identity],
        quality_gate: Identity,
        workspace: Path,
        validators: dict[str, Validator],
        *,
        workspaces: dict[str, Path] | None = None,
        local_approvers: frozenset[str] = frozenset(),
    ) -> None:
        broker._identity(orchestrator)
        broker._identity(quality_gate)
        if (
            orchestrator.role != Role.ORCHESTRATOR
            or quality_gate.role != Role.QUALITY_GATE
        ):
            raise PermissionError("orchestrator and quality-gate identities required")
        self.broker = broker
        self.manager = manager
        self.orchestrator = orchestrator
        self.quality_gate = quality_gate
        self.agents = agents
        self.workspace = workspace.resolve(strict=True)
        self.workspaces = {
            name: path.resolve(strict=True) for name, path in (workspaces or {}).items()
        }
        self.validators = validators
        self.local_approvers = local_approvers
        self.repository = WorkflowRepository(broker.db)
        self._active: set[str] = set()
        self._tasks: dict[str, asyncio.Task[WorkflowRun]] = {}

    def create(
        self, definition: WorkflowDefinition, run_id: str | None = None
    ) -> WorkflowRun:
        validate(definition)
        for step in definition.steps:
            if step.type == "agent" and (
                step.agent not in self.agents
                or step.executor not in self.manager.adapters
            ):
                raise ValueError("agent or executor is not registered")
            if step.type == "agent" and step.agent in self.agents:
                agent = self.agents[step.agent]
                if agent.role == Role.QA and (
                    step.sandbox_mode != "read-only"
                    or not step.inputs.get("artifact_id")
                ):
                    raise ValueError("QA needs read-only mode and artifact_id")
            if step.type == "validation" and step.validator not in self.validators:
                raise ValueError("validator is not registered")
        run = self.repository.create(run_id or str(uuid4()), definition)
        self.repository.workflow_status(run.run_id, WorkflowStatus.VALIDATED)
        return self.repository.get_run(run.run_id)

    def create_published(
        self,
        registry: MethodologyRegistry,
        methodology_id: str,
        version: str,
        run_id: str | None = None,
    ) -> WorkflowRun:
        definition = registry.get(methodology_id, version).definition
        run = self.create(definition, run_id)
        registry.bind_run(run.run_id, methodology_id, version)
        return run

    def _ready(
        self, definition: WorkflowDefinition, steps: dict[str, StepRun]
    ) -> list[WorkflowStep]:
        eligible = []
        by_id = {step.id: step for step in definition.steps}
        for step_id in validate(definition):
            step = by_id[step_id]
            current = steps[step.id]
            if current.status != StepStatus.PENDING:
                continue
            dependencies = [steps[dep] for dep in step.depends_on]
            if any(
                dep.status
                in {
                    StepStatus.FAILED,
                    StepStatus.SKIPPED,
                    StepStatus.CANCELLED,
                    StepStatus.INTERRUPTED,
                }
                for dep in dependencies
            ):
                self.repository.step_status(current.run_id, step.id, StepStatus.SKIPPED)
                current.status = StepStatus.SKIPPED
                continue
            if not all(dep.status == StepStatus.SUCCEEDED for dep in dependencies):
                continue
            if step.condition:
                value = steps[step.condition.step].result
                if value is None or not isinstance(value.get("passed"), bool):
                    raise RuntimeError("condition source lacks a typed result")
                if value["passed"] != step.condition.result_equals:
                    self.repository.step_status(
                        current.run_id, step.id, StepStatus.SKIPPED
                    )
                    current.status = StepStatus.SKIPPED
                    continue
            eligible.append(step)
        return eligible

    def _inputs(self, run_id: str, step: WorkflowStep) -> dict[str, str]:
        results = self.repository.steps(run_id)

        def replace(match: re.Match[str]) -> str:
            value = results[match.group(1)].result
            if value is None or not isinstance(value.get("output"), str):
                raise RuntimeError("referenced output is missing")
            output = value["output"]
            assert isinstance(output, str)
            return output

        return {
            key: re.sub(r"\{\{([A-Za-z0-9_-]+)\.output\}\}", replace, value)
            for key, value in step.inputs.items()
        }

    async def _execute(self, run_id: str, step: WorkflowStep) -> None:
        self.repository.step_status(run_id, step.id, StepStatus.READY)
        if step.type == "human_approval":
            self.repository.step_status(run_id, step.id, StepStatus.WAITING_APPROVAL)
            return
        if step.type == "validation":
            await self._validate_step(run_id, step)
            return
        await self._agent_step(run_id, step)

    async def _validate_step(self, run_id: str, step: WorkflowStep) -> None:
        assert step.validator is not None
        for _ in range(step.retry_policy.max_attempts):
            self.repository.step_status(run_id, step.id, StepStatus.RUNNING)
            try:
                passed = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.validators[step.validator], self._inputs(run_id, step)
                    ),
                    timeout=step.timeout_seconds or 120,
                )
                if not isinstance(passed, bool):
                    raise TypeError("validator must return bool")
                self.broker.publish(
                    Message(
                        protocol="ACP",
                        version="0.1",
                        message_id=f"validation:{run_id}:{step.id}:{self.repository.steps(run_id)[step.id].attempt}",
                        run_id=run_id,
                        task_id=step.id,
                        sender=self.quality_gate.agent_id,
                        recipient=self.orchestrator.agent_id,
                        type=MessageType.VALIDATION_RESULT,
                        iteration=0,
                        created_at=datetime.now(timezone.utc),
                        payload=ValidationResult(
                            artifact_id=f"workflow:{run_id}:{step.id}",
                            passed=passed,
                            evidence=f"registered validator: {step.validator}",
                        ),
                    ),
                    self.quality_gate,
                )
                self.repository.step_status(
                    run_id, step.id, StepStatus.SUCCEEDED, result={"passed": passed}
                )
                return
            except TransientValidationError as exc:
                if (
                    self.repository.steps(run_id)[step.id].attempt
                    < step.retry_policy.max_attempts
                ):
                    self.repository.step_status(
                        run_id,
                        step.id,
                        StepStatus.READY,
                        result={"error": type(exc).__name__},
                    )
                    continue
                self.repository.step_status(
                    run_id,
                    step.id,
                    StepStatus.FAILED,
                    result={"error": type(exc).__name__},
                )
                return
            except Exception as exc:
                self.repository.step_status(
                    run_id,
                    step.id,
                    StepStatus.FAILED,
                    result={"error": type(exc).__name__},
                )
                return

    async def _agent_step(self, run_id: str, step: WorkflowStep) -> None:
        assert step.agent is not None and step.executor is not None
        identity = self.agents[step.agent]
        if identity.role not in {Role.IMPLEMENTER, Role.QA}:
            raise PermissionError("agent steps require implementer or QA role")
        prompt = self._inputs(run_id, step)["prompt"]
        attempt = self.repository.steps(run_id)[step.id].attempt + 1
        workflow_metadata = self.repository.definition(run_id).metadata
        iteration = int(workflow_metadata.get("iteration", "0"))
        execution_id = f"workflow:{run_id}:{step.id}:{attempt}"
        message_id = f"assignment:{run_id}:{step.id}:{attempt}"
        self.repository.step_status(
            run_id, step.id, StepStatus.RUNNING, execution_id=execution_id
        )
        is_review = identity.role == Role.QA
        message = Message(
            protocol="ACP",
            version="0.1",
            message_id=message_id,
            run_id=run_id,
            task_id=step.id,
            sender=self.orchestrator.agent_id,
            recipient=identity.agent_id,
            type=MessageType.REVIEW_REQUEST if is_review else MessageType.TASK_ASSIGNED,
            iteration=iteration,
            created_at=datetime.now(timezone.utc),
            payload=(
                ReviewRequest(
                    artifact_id=self._inputs(run_id, step)["artifact_id"],
                    criteria=prompt,
                )
                if is_review
                else TaskAssigned(title=step.id, instructions=prompt)
            ),
            metadata={
                "workflow_step": step.id,
                "workspace_write": step.sandbox_mode == "workspace-write",
                **workflow_metadata,
            },
        )
        try:
            self.broker.publish(message, self.orchestrator)
            result = await self.manager.execute(
                ExecutionRequest(
                    execution_id=execution_id,
                    run_id=run_id,
                    task_id=step.id,
                    agent_id=identity.agent_id,
                    executor=step.executor,
                    prompt=prompt,
                    working_directory=self.workspaces.get(step.agent, self.workspace),
                    sandbox_mode=step.sandbox_mode,
                    timeout_seconds=step.timeout_seconds or 120,
                ),
                identity,
                message_id,
            )
        except asyncio.CancelledError:
            status = (
                StepStatus.CANCELLED
                if self.repository.get_run(run_id).status == WorkflowStatus.CANCELLED
                else StepStatus.INTERRUPTED
            )
            self.repository.step_status(
                run_id, step.id, status, result={"error": "cancelled"}
            )
            raise
        except Exception as exc:
            self.repository.step_status(
                run_id,
                step.id,
                StepStatus.INTERRUPTED,
                result={"error": type(exc).__name__},
            )
            return
        self._finish_agent(run_id, step.id, result)

    def _finish_agent(self, run_id: str, step_id: str, result: ExecutionResult) -> None:
        if result.status == ExecutionStatus.SUCCEEDED:
            message = self.broker.get_message(f"execution:{result.execution_id}")
            attempt = self.repository.steps(run_id)[step_id].attempt
            expected_assignment = f"assignment:{run_id}:{step_id}:{attempt}"
            if (
                message is None
                or message.type
                not in (
                    {MessageType.VALIDATION_RESULT, MessageType.FINDING_REPORTED}
                    if result.agent_id
                    in {
                        agent.agent_id
                        for agent in self.agents.values()
                        if agent.role == Role.QA
                    }
                    else {MessageType.ARTIFACT_SUBMITTED}
                )
                or message.run_id != run_id
                or message.task_id != step_id
                or message.sender != result.agent_id
                or message.recipient != self.orchestrator.agent_id
                or message.causation_id != expected_assignment
                or message.metadata.get("execution_id") != result.execution_id
                or message.artifact_refs != result.artifact_refs
            ):
                self.repository.step_status(
                    run_id,
                    step_id,
                    StepStatus.INTERRUPTED,
                    result={"error": "missing or mismatched ACP result"},
                )
                return
        status = (
            StepStatus.SUCCEEDED
            if result.status == ExecutionStatus.SUCCEEDED
            else StepStatus.FAILED
        )
        self.repository.step_status(
            run_id,
            step_id,
            status,
            result={
                "output": result.output if status == StepStatus.SUCCEEDED else "",
                "error": result.error,
                "execution_status": result.status.value,
            },
        )

    def _fail(self, run_id: str, reason: str) -> None:
        self.repository.workflow_status(run_id, WorkflowStatus.FAILED, reason)
        self._publish_failure(run_id)

    def _publish_failure(self, run_id: str) -> None:
        run = self.repository.get_run(run_id)
        message_id = f"workflow-failed:{run_id}"
        if self.broker.get_message(message_id) is not None:
            return
        self.broker.publish(
            Message(
                protocol="ACP",
                version="0.1",
                message_id=message_id,
                run_id=run_id,
                task_id="workflow",
                sender=self.orchestrator.agent_id,
                recipient=self.quality_gate.agent_id,
                type=MessageType.TASK_FAILED,
                iteration=0,
                created_at=run.finished_at or datetime.now(timezone.utc),
                payload=TaskFailed(reason=run.error or "workflow step failed"),
            ),
            self.orchestrator,
        )

    async def run(self, run_id: str) -> WorkflowRun:
        if run_id in self._active:
            raise RuntimeError("workflow is already running in this process")
        async with held(self.broker.db.path, f"workflow:{run_id}"):
            return await self._run_owned(run_id)

    async def _run_owned(self, run_id: str) -> WorkflowRun:
        if run_id in self._active:
            raise RuntimeError("workflow is already running in this process")
        self._active.add(run_id)
        task = asyncio.current_task()
        if task is not None:
            self._tasks[run_id] = task
        try:
            definition = self.repository.definition(run_id)
            run = self._recover(run_id)
            if run.status == WorkflowStatus.VALIDATED:
                self.repository.workflow_status(run_id, WorkflowStatus.RUNNING)
            elif run.status not in {
                WorkflowStatus.RUNNING,
                WorkflowStatus.WAITING_APPROVAL,
            }:
                raise ValueError("workflow cannot run in this state")
            if run.status == WorkflowStatus.WAITING_APPROVAL:
                return run
            while True:
                run = self.repository.get_run(run_id)
                if (
                    datetime.now(timezone.utc) - (run.started_at or run.created_at)
                ).total_seconds() >= definition.limits.max_duration_seconds:
                    self._fail(run_id, "workflow timeout")
                    return self.repository.get_run(run_id)
                steps = self.repository.steps(run_id)
                if any(s.status == StepStatus.INTERRUPTED for s in steps.values()):
                    self.repository.workflow_status(run_id, WorkflowStatus.INTERRUPTED)
                    return self.repository.get_run(run_id)
                if any(s.status == StepStatus.FAILED for s in steps.values()):
                    self._fail(run_id, "workflow step failed")
                    return self.repository.get_run(run_id)
                ready = self._ready(definition, steps)
                if ready:
                    for offset in range(
                        0, len(ready), definition.limits.max_parallel_steps
                    ):
                        remaining = (
                            definition.limits.max_duration_seconds
                            - (
                                datetime.now(timezone.utc)
                                - (run.started_at or run.created_at)
                            ).total_seconds()
                        )
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(
                                    *(
                                        self._execute(run_id, step)
                                        for step in ready[
                                            offset : offset
                                            + definition.limits.max_parallel_steps
                                        ]
                                    )
                                ),
                                timeout=max(0, remaining),
                            )
                        except TimeoutError:
                            self._fail(run_id, "workflow timeout")
                            return self.repository.get_run(run_id)
                    continue
                steps = self.repository.steps(run_id)
                if any(s.status == StepStatus.WAITING_APPROVAL for s in steps.values()):
                    self.repository.workflow_status(
                        run_id, WorkflowStatus.WAITING_APPROVAL
                    )
                elif all(
                    s.status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED}
                    for s in steps.values()
                ):
                    required_approval_skipped = any(
                        step.type == "human_approval"
                        and steps[step.id].status == StepStatus.SKIPPED
                        for step in definition.steps
                    )
                    if required_approval_skipped:
                        self._fail(run_id, "required approval was skipped")
                    else:
                        self.repository.workflow_status(
                            run_id, WorkflowStatus.COMPLETED
                        )
                else:
                    raise RuntimeError("workflow has no eligible step")
                return self.repository.get_run(run_id)
        finally:
            self._active.remove(run_id)
            self._tasks.pop(run_id, None)

    def decide(
        self, run_id: str, step_id: str, actor: str, approved: bool
    ) -> WorkflowRun:
        if actor not in self.local_approvers:
            raise PermissionError("local approver is not authorized")
        if self.recover(run_id).status != WorkflowStatus.WAITING_APPROVAL:
            raise ValueError("workflow is not waiting for approval")
        self.repository.decide(run_id, step_id, actor, approved)
        if approved:
            self.repository.workflow_status(run_id, WorkflowStatus.RUNNING)
        else:
            self._fail(run_id, "human approval rejected")
        return self.repository.get_run(run_id)

    def recover(self, run_id: str) -> WorkflowRun:
        lock = LocalLock(self.broker.db.path, f"workflow:{run_id}")
        try:
            if not lock.try_acquire():
                return self.repository.get_run(run_id)
            return self._recover(run_id)
        finally:
            lock.close()

    def _recover(self, run_id: str) -> WorkflowRun:
        run = self.repository.get_run(run_id)
        if run.status == WorkflowStatus.FAILED:
            self._publish_failure(run_id)
            return run
        if (
            run.status == WorkflowStatus.WAITING_APPROVAL
            and (
                datetime.now(timezone.utc) - (run.started_at or run.created_at)
            ).total_seconds()
            >= self.repository.definition(run_id).limits.max_duration_seconds
        ):
            self._fail(run_id, "workflow timeout")
            return self.repository.get_run(run_id)
        if run.status == WorkflowStatus.WAITING_APPROVAL:
            decisions = [
                self.repository.approval_status(run_id, step.step_id)
                for step in self.repository.steps(run_id).values()
                if step.status in {StepStatus.SUCCEEDED, StepStatus.FAILED}
            ]
            if "REJECTED" in decisions:
                self._fail(run_id, "human approval rejected")
            elif "APPROVED" in decisions:
                self.repository.workflow_status(run_id, WorkflowStatus.RUNNING)
            return self.repository.get_run(run_id)
        if run.status != WorkflowStatus.RUNNING:
            return run
        for step in self.repository.steps(run_id).values():
            if step.status != StepStatus.RUNNING:
                continue
            definition = self.repository.definition(run_id)
            static_step = next(s for s in definition.steps if s.id == step.step_id)
            if static_step.type == "validation":
                message = self.broker.get_message(
                    f"validation:{run_id}:{step.step_id}:{step.attempt}"
                )
                if (
                    message
                    and isinstance(message.payload, ValidationResult)
                    and message.type == MessageType.VALIDATION_RESULT
                    and message.run_id == run_id
                    and message.task_id == step.step_id
                    and message.sender == self.quality_gate.agent_id
                    and message.recipient == self.orchestrator.agent_id
                ):
                    self.repository.step_status(
                        run_id,
                        step.step_id,
                        StepStatus.SUCCEEDED,
                        result={"passed": message.payload.passed},
                    )
                    continue
            if step.execution_id:
                previous = self.manager.repository.get(step.execution_id)
                if isinstance(previous, ExecutionResult):
                    self._finish_agent(run_id, step.step_id, previous)
                    continue
            self.repository.step_status(
                run_id,
                step.step_id,
                StepStatus.INTERRUPTED,
                result={"error": "unknown outcome"},
            )
        if any(
            s.status == StepStatus.INTERRUPTED
            for s in self.repository.steps(run_id).values()
        ):
            self.repository.workflow_status(run_id, WorkflowStatus.INTERRUPTED)
        return self.repository.get_run(run_id)

    def cancel(self, run_id: str) -> WorkflowRun:
        if run_id in self._active:
            return self._cancel_owned(run_id)
        lock = LocalLock(self.broker.db.path, f"workflow:{run_id}")
        try:
            if not lock.try_acquire():
                raise RuntimeError("workflow is active in another engine")
            return self._cancel_owned(run_id)
        finally:
            lock.close()

    def _cancel_owned(self, run_id: str) -> WorkflowRun:
        run = self.repository.get_run(run_id)
        if run.status not in {
            WorkflowStatus.CREATED,
            WorkflowStatus.VALIDATED,
            WorkflowStatus.RUNNING,
            WorkflowStatus.WAITING_APPROVAL,
            WorkflowStatus.INTERRUPTED,
        }:
            raise ValueError("workflow is terminal")
        for step in self.repository.steps(run_id).values():
            if step.status in {
                StepStatus.PENDING,
                StepStatus.READY,
                StepStatus.WAITING_APPROVAL,
            }:
                self.repository.step_status(run_id, step.step_id, StepStatus.CANCELLED)
        self.repository.workflow_status(run_id, WorkflowStatus.CANCELLED)
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
        return self.repository.get_run(run_id)
