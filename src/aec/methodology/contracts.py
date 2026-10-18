"""Strict records for supervised methodology changes."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aec.communication.schemas import ArtifactRef
from aec.workflows.models import WorkflowDefinition


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ProposalStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_EVALUATION = "UNDER_EVALUATION"
    EVALUATION_FAILED = "EVALUATION_FAILED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


class EvaluationVerdict(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class Change(Contract):
    operation: Literal["add_quality_gate"]
    gate: Literal["type_check"]
    position: Literal["before_qa"]


class MethodologyVersion(Contract):
    methodology_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]
    version: str
    definition: WorkflowDefinition
    mandatory_gates: frozenset[str]

    @model_validator(mode="after")
    def valid_version(self) -> MethodologyVersion:
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.version):
            raise ValueError("semantic version required")
        if (self.definition.id, self.definition.version) != (
            self.methodology_id,
            self.version,
        ):
            raise ValueError("definition ID and version must match")
        validators = {
            step.validator
            for step in self.definition.steps
            if step.type == "validation"
        }
        if not self.mandatory_gates <= validators:
            raise ValueError("mandatory gates must be validation steps")
        return self


class MethodologyObservation(Contract):
    run_id: str
    task_id: str
    methodology_id: str
    version: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    review_iterations: Annotated[int, Field(ge=0)] | None
    first_review_passed: bool | None
    required_rework: bool | None
    findings: tuple[str, ...]
    message_ids: tuple[str, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    gates_passed: bool | None
    human_interventions: Annotated[int, Field(ge=0)] | None
    agent_invocations: Annotated[int, Field(ge=0)] | None
    approval_at: datetime | None


class MethodologyMetric(Contract):
    name: str
    numerator: float | None
    denominator: float | None
    value: float | None
    unit: str
    window_start: datetime | None
    window_end: datetime | None
    methodology_id: str
    version: str
    source: str
    missing: str | None = None
    limitation: str


class MethodologyFinding(Contract):
    finding_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_:-]+$")]
    category: Annotated[str, Field(min_length=1)]
    observation: Annotated[str, Field(min_length=1)]
    hypothesis: Annotated[str, Field(min_length=1)]
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class MethodologyProposal(Contract):
    proposal_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]
    methodology_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]
    base_version: str
    proposed_version: str
    problem: Annotated[str, Field(min_length=1)]
    hypothesis: Annotated[str, Field(min_length=1)]
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    changes: tuple[Change, ...] = Field(min_length=1)
    expected_metric: Annotated[str, Field(min_length=1)]
    expected_direction: Literal["increase", "decrease"]
    risks: Annotated[str, Field(min_length=1)]
    validation_criteria: Annotated[str, Field(min_length=1)]
    rollback_plan: Annotated[str, Field(min_length=1)]
    requires_human_approval: Literal[True] = True

    @model_validator(mode="after")
    def increasing_version(self) -> MethodologyProposal:
        def parts(value: str) -> tuple[int, int, int]:
            if not re.fullmatch(r"\d+\.\d+\.\d+", value):
                raise ValueError("semantic version required")
            major, minor, patch = value.split(".")
            return int(major), int(minor), int(patch)

        if parts(self.proposed_version) <= parts(self.base_version):
            raise ValueError("proposed version must increase")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("duplicate evidence reference")
        return self


class BenchmarkCase(Contract):
    task_id: Annotated[str, Field(min_length=1)]
    acceptance_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    conditions: Annotated[str, Field(min_length=1)]


class BenchmarkPair(BenchmarkCase):
    baseline: Annotated[float, Field(allow_inf_nan=False)]
    candidate: Annotated[float, Field(allow_inf_nan=False)]


class MethodologyEvaluation(Contract):
    evaluation_id: str
    proposal_id: str
    proposal_sha256: str
    verdict: EvaluationVerdict
    checks: tuple[str, ...]
    limitations: tuple[str, ...]
    pairs: tuple[BenchmarkPair, ...] = ()
    baseline: float | None = None
    candidate: float | None = None


class MethodologyApproval(Contract):
    proposal_id: str
    proposal_sha256: str
    actor: str
    decided_at: datetime
    decision: Literal["APPROVED", "REJECTED", "CHANGES_REQUESTED"]


class MethodologyPublication(Contract):
    proposal_id: str
    methodology_id: str
    version: str
    sha256: str
    approved_by: str
    published_at: datetime
