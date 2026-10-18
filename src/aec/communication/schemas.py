"""Validated wire contracts for ACP v0.1."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MessageType(StrEnum):
    TASK_ASSIGNED = "TASK_ASSIGNED"
    ARTIFACT_SUBMITTED = "ARTIFACT_SUBMITTED"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    FINDING_REPORTED = "FINDING_REPORTED"
    REWORK_REQUEST = "REWORK_REQUEST"
    VALIDATION_RESULT = "VALIDATION_RESULT"
    METHODOLOGY_PROPOSAL = "METHODOLOGY_PROPOSAL"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    DOCUMENTATION_REQUEST = "DOCUMENTATION_REQUEST"
    DOCUMENTATION_GENERATED = "DOCUMENTATION_GENERATED"
    DOCUMENTATION_FAILED = "DOCUMENTATION_FAILED"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ArtifactRef(Contract):
    artifact_id: Annotated[str, Field(min_length=1)]
    kind: Annotated[str, Field(min_length=1)]
    uri: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_by: Annotated[str, Field(min_length=1)]

    @field_validator("uri")
    @classmethod
    def local_artifact_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        parts = unquote(parsed.path).split("/")
        if (
            parsed.scheme != "artifact"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or any(part in ("", ".", "..") for part in parts[1:])
            or not parsed.path.startswith("/")
        ):
            raise ValueError("expected artifact://<run>/<path> without traversal")
        return value


class TaskAssigned(Contract):
    title: Annotated[str, Field(min_length=1)]
    instructions: Annotated[str, Field(min_length=1)]


class ArtifactSubmitted(Contract):
    summary: Annotated[str, Field(min_length=1)]
    artifact_id: Annotated[str, Field(min_length=1)]


class ReviewRequest(Contract):
    artifact_id: Annotated[str, Field(min_length=1)]
    criteria: Annotated[str, Field(min_length=1)]


class FindingReported(Contract):
    artifact_id: Annotated[str, Field(min_length=1)]
    severity: Literal["low", "medium", "high", "critical"]
    description: Annotated[str, Field(min_length=1)]


class ReworkRequest(Contract):
    finding_message_id: Annotated[str, Field(min_length=1)]
    instructions: Annotated[str, Field(min_length=1)]


class ValidationResult(Contract):
    artifact_id: Annotated[str, Field(min_length=1)]
    passed: bool
    evidence: Annotated[str, Field(min_length=1)]


class MethodologyProposal(Contract):
    title: Annotated[str, Field(min_length=1)]
    rationale: Annotated[str, Field(min_length=1)]


class ApprovalRequest(Contract):
    subject: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]


class TaskCompleted(Contract):
    summary: Annotated[str, Field(min_length=1)]
    validation_message_id: Annotated[str, Field(min_length=1)]


class TaskFailed(Contract):
    reason: Annotated[str, Field(min_length=1)]


class DocumentationRequest(Contract):
    run_id: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    documentation_type: Literal[
        "execution-report",
        "qa-summary",
        "change-summary",
        "adr",
        "technical-documentation",
    ]
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    target_scope: Annotated[str, Field(min_length=1)]
    requested_by: Annotated[str, Field(min_length=1)]
    correlation_id: Annotated[str, Field(min_length=1)]


class DocumentationResult(Contract):
    document_id: Annotated[str, Field(min_length=1)]
    document_type: Annotated[str, Field(min_length=1)]
    generated_artifact_ref: ArtifactRef
    source_refs: list[ArtifactRef] = Field(default_factory=list)
    validation_status: Literal["VALID", "VALID_WITH_WARNINGS", "INVALID"]
    warnings: list[str] = Field(default_factory=list)
    timestamp: datetime


class DocumentationFailure(Contract):
    request_message_id: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]


Payload = (
    TaskAssigned
    | ArtifactSubmitted
    | ReviewRequest
    | FindingReported
    | ReworkRequest
    | ValidationResult
    | MethodologyProposal
    | ApprovalRequest
    | TaskCompleted
    | TaskFailed
    | DocumentationRequest
    | DocumentationResult
    | DocumentationFailure
)

PAYLOADS: dict[MessageType, type[Contract]] = {
    MessageType.TASK_ASSIGNED: TaskAssigned,
    MessageType.ARTIFACT_SUBMITTED: ArtifactSubmitted,
    MessageType.REVIEW_REQUEST: ReviewRequest,
    MessageType.FINDING_REPORTED: FindingReported,
    MessageType.REWORK_REQUEST: ReworkRequest,
    MessageType.VALIDATION_RESULT: ValidationResult,
    MessageType.METHODOLOGY_PROPOSAL: MethodologyProposal,
    MessageType.APPROVAL_REQUEST: ApprovalRequest,
    MessageType.TASK_COMPLETED: TaskCompleted,
    MessageType.TASK_FAILED: TaskFailed,
    MessageType.DOCUMENTATION_REQUEST: DocumentationRequest,
    MessageType.DOCUMENTATION_GENERATED: DocumentationResult,
    MessageType.DOCUMENTATION_FAILED: DocumentationFailure,
}


class Message(Contract):
    protocol: Literal["ACP"]
    version: Literal["0.1"]
    message_id: Annotated[str, Field(min_length=1)]
    run_id: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    sender: Annotated[str, Field(min_length=1)]
    recipient: Annotated[str, Field(min_length=1)]
    type: MessageType
    iteration: Annotated[int, Field(ge=0)]
    created_at: datetime
    payload: Payload
    correlation_id: str | None = None
    causation_id: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("type", mode="before")
    @classmethod
    def parse_wire_type(cls, value: Any) -> Any:
        return MessageType(value) if isinstance(value, str) else value

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_wire_datetime(cls, value: Any) -> Any:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @model_validator(mode="before")
    @classmethod
    def validate_payload_type(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("type") in PAYLOADS:
            expected = PAYLOADS[MessageType(data["type"])]
            payload = data.get("payload")
            data = dict(data)
            data["payload"] = (
                expected.model_validate_json(json.dumps(payload))
                if isinstance(payload, dict)
                else expected.model_validate(payload)
            )
        return data

    @model_validator(mode="after")
    def validate_envelope(self) -> Message:
        if not isinstance(self.payload, PAYLOADS[self.type]):
            raise ValueError("payload does not match message type")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        if any(ref.created_by != self.sender for ref in self.artifact_refs):
            raise ValueError("artifact creator must match sender")
        if any(urlsplit(ref.uri).netloc != self.run_id for ref in self.artifact_refs):
            raise ValueError("artifact URI must belong to run_id")
        if isinstance(
            self.payload, ArtifactSubmitted
        ) and self.payload.artifact_id not in {
            ref.artifact_id for ref in self.artifact_refs
        }:
            raise ValueError("submitted artifact requires a matching reference")
        if len(self.model_dump_json().encode("utf-8")) > 65_536:
            raise ValueError("message exceeds 64 KiB limit")
        return self
