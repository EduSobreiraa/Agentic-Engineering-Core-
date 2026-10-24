"""Deterministic selection of published, locally runnable methodologies."""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aec.execution.policy import ExecutionPolicy
from aec.methodology.governance import (
    BLOCKED_METHODOLOGY_VERSIONS,
    MethodologyRegistry,
)

TaskType = Literal[
    "bug",
    "feature",
    "alternatives",
    "security_audit",
    "architecture_decision",
    "methodology_improvement",
]

PREFERRED: dict[str, str] = {
    "bug": "tdd-adversarial",
    "feature": "dev-adversarial",
    "alternatives": "implementation-arena",
    "security_audit": "security-audit",
    "architecture_decision": "decision-adversarial",
}


@dataclass(frozen=True)
class RouteRequest:
    task_type: TaskType
    description: str
    acceptance_criteria: tuple[str, ...]
    override: str | None = None
    actor: str | None = None
    project_path: Path | None = None
    benchmark_argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeReadiness:
    agents: frozenset[str]
    executors: frozenset[str]
    validators: frozenset[str]
    max_agent_invocations: int
    max_parallel_agents: int = 1
    approval_bindings: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RouteResult:
    methodology_id: str
    version: str
    reason: str
    agents: tuple[str, ...]
    dependencies: tuple[str, ...]
    risks: tuple[str, ...]
    readiness: Literal["READY"]
    max_agent_invocations: int


class RouteBlocked(ValueError):
    """No published and ready methodology can satisfy the request."""


class MethodologyRouter:
    def __init__(
        self,
        registry: MethodologyRegistry,
        readiness: RuntimeReadiness,
        *,
        local_approvers: frozenset[str] = frozenset(),
        audit_policy: ExecutionPolicy | None = None,
    ) -> None:
        self.registry = registry
        self.readiness = readiness
        self.local_approvers = local_approvers
        self.audit_policy = audit_policy

    def route(self, request: RouteRequest) -> RouteResult:
        if not request.description.strip() or not request.acceptance_criteria:
            raise ValueError("description and acceptance criteria required")
        if request.override is not None and request.actor not in self.local_approvers:
            raise PermissionError(
                "manual methodology selection requires a local approver"
            )
        if request.task_type == "methodology_improvement":
            raise RouteBlocked("methodology changes require Phase 6 governance")
        try:
            selected = request.override or PREFERRED[request.task_type]
        except KeyError as exc:
            raise ValueError("unknown task type") from exc
        try:
            version = self.registry.active(selected)
        except KeyError as exc:
            raise RouteBlocked(f"methodology is not published: {selected}") from exc
        if (version.methodology_id, version.version) in BLOCKED_METHODOLOGY_VERSIONS:
            raise RouteBlocked("methodology version is blocked by artifact integrity")
        definition = version.definition
        binding = definition.metadata.get("approval_binding")
        if binding and binding not in self.readiness.approval_bindings:
            raise RouteBlocked("approval integrity binding is unavailable")
        arena = selected == "implementation-arena"
        if arena:
            controller = definition.metadata.get("controller")
            if controller not in {"sequential-arena-v1", "sequential-arena-v2"}:
                raise RouteBlocked("unsupported Arena controller")
            if controller == "sequential-arena-v2":
                if not request.benchmark_argv:
                    raise RouteBlocked("Arena benchmark command is required")
                if sys.platform != "linux" or not Path("/usr/bin/time").is_file():
                    raise RouteBlocked("Arena resource measurement is unavailable")
        agents = tuple(
            sorted(
                {step.agent for step in definition.steps if step.agent}
                | ({"implementer_a", "implementer_b", "qa"} if arena else set())
            )
        )
        executors = {step.executor for step in definition.steps if step.executor}
        if arena:
            executors.add("codex")
        validators = {step.validator for step in definition.steps if step.validator}
        missing = (
            (
                {f"agent:{name}" for name in agents}
                - {f"agent:{name}" for name in self.readiness.agents}
            )
            | (
                {f"executor:{name}" for name in executors}
                - {f"executor:{name}" for name in self.readiness.executors}
            )
            | (
                {f"validator:{name}" for name in validators}
                - {f"validator:{name}" for name in self.readiness.validators}
            )
        )
        if missing:
            raise RouteBlocked(
                f"dependencies unavailable: {', '.join(sorted(missing))}"
            )
        invocations = (
            3 if arena else sum(step.type == "agent" for step in definition.steps)
        )
        if (
            invocations > self.readiness.max_agent_invocations
            or definition.limits.max_parallel_steps > self.readiness.max_parallel_agents
        ):
            raise RouteBlocked("resource limits do not permit this methodology")
        if selected == "security-audit":
            if self.audit_policy is None or request.project_path is None:
                raise RouteBlocked(
                    "security audit requires an authorized local project"
                )
            try:
                self.audit_policy.require(request.project_path)
            except (PermissionError, ValueError, OSError) as exc:
                raise RouteBlocked("security audit project is not authorized") from exc
        risks = (
            ("Static review only; no sandbox for untrusted code.",)
            if selected == "security-audit"
            else ("Local approver identity is not remotely authenticated.",)
        )
        return RouteResult(
            methodology_id=selected,
            version=version.version,
            reason=(
                f"Authorized manual selection by {request.actor}"
                if request.override is not None
                else f"Task type {request.task_type} maps to {selected}"
            ),
            agents=agents,
            dependencies=tuple(sorted(f"executor:{name}" for name in executors))
            + tuple(sorted(f"validator:{name}" for name in validators))
            + (
                ("benchmark:declared", "tool:gnu-time")
                if arena and controller == "sequential-arena-v2"
                else ()
            ),
            risks=risks,
            readiness="READY",
            max_agent_invocations=invocations,
        )
