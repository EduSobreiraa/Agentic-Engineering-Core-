"""Static workflow definitions and persisted run snapshots."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from aec.communication.schemas import ArtifactRef


class WorkflowStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 1


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    step: str
    result_equals: bool


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]
    type: Literal["agent", "validation", "human_approval"]
    agent: str | None = None
    executor: str | None = None
    sandbox_mode: Literal["read-only", "workspace-write"] = "read-only"
    validator: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    condition: Condition | None = None
    timeout_seconds: Annotated[float, Field(gt=0)] | None = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)


class WorkflowLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_parallel_steps: Annotated[int, Field(ge=1)] = 1
    max_total_steps: Annotated[int, Field(ge=1)] = 100
    max_duration_seconds: Annotated[float, Field(gt=0)] = 1800


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]
    version: str
    name: Annotated[str, Field(min_length=1)]
    description: str = ""
    steps: list[WorkflowStep]
    limits: WorkflowLimits = Field(default_factory=WorkflowLimits)
    metadata: dict[str, str] = Field(default_factory=dict)


class WorkflowTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requirements: Annotated[str, Field(min_length=1)]
    acceptance_criteria: Annotated[
        tuple[Annotated[str, Field(min_length=1)], ...], Field(min_length=1)
    ]
    artifact_refs: tuple[ArtifactRef, ...] = ()


class WorkflowRun(BaseModel):
    run_id: str
    workflow_id: str
    workflow_version: str
    definition_hash: str
    status: WorkflowStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class StepRun(BaseModel):
    step_run_id: str
    run_id: str
    step_id: str
    status: StepStatus
    attempt: int = 0
    execution_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, str | bool | None] | None = None
