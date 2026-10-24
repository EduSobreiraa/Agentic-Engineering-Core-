"""Public execution contracts."""

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from aec.communication.schemas import ArtifactRef


class ExecutionStatus(StrEnum):
    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    _process_observer: Callable[[str, int], None] | None = PrivateAttr(default=None)
    _scope_observer: Callable[[str, str], None] | None = PrivateAttr(default=None)

    execution_id: Annotated[str, Field(min_length=1)]
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+$")]
    task_id: Annotated[str, Field(min_length=1)]
    agent_id: Annotated[str, Field(min_length=1)]
    executor: Annotated[str, Field(min_length=1)]
    prompt: Annotated[str, Field(min_length=1)]
    working_directory: Path
    sandbox_mode: Literal["read-only", "workspace-write"] = "read-only"
    session_id: str | None = None
    timeout_seconds: Annotated[float, Field(gt=0)] = 120
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("working_directory")
    @classmethod
    def existing_directory(cls, value: Path) -> Path:
        if not value.is_dir():
            raise ValueError("working_directory must be an existing directory")
        return value.resolve()


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    execution_id: str
    agent_id: str
    status: ExecutionStatus
    session_id: str | None = None
    output: str = ""
    exit_code: int | None = None
    started_at: datetime
    finished_at: datetime
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    error: str | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)
    stdout: str = Field(default="", exclude=True)
    stderr: str = Field(default="", exclude=True)


class AgentSession(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str
    provider_session_id: str
    executor: str
    agent_id: str
    working_directory: Path
    state: ExecutionStatus
    created_at: datetime
    last_used_at: datetime
