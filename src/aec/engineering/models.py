from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aec.communication.schemas import ArtifactRef

Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class ReviewFinding(Contract):
    finding_id: Annotated[str, Field(min_length=1)]
    severity: Severity
    category: Annotated[str, Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    description: Annotated[str, Field(min_length=1)]
    file: str | None = None
    line: Annotated[int, Field(ge=1)] | None = None
    reproduction_steps: list[str] = Field(default_factory=list)
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    evidence_refs: list[ArtifactRef] = Field(default_factory=list)
    status: Literal["REPRODUCED", "HYPOTHESIS"]

    @model_validator(mode="after")
    def reproduced_has_reproduction(self) -> ReviewFinding:
        if self.status == "REPRODUCED" and not (
            self.reproduction_steps or self.evidence_refs
        ):
            raise ValueError("reproduced finding needs steps or evidence")
        return self


class ImplementationResult(Contract):
    task_id: Annotated[str, Field(min_length=1)]
    iteration: Annotated[int, Field(ge=1)]
    status: Literal["SUCCEEDED", "FAILED"]
    base_commit: Commit
    result_commit: Commit
    changed_files: list[str]
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    test_results: list[str] = Field(default_factory=list)
    implementation_summary: str
    known_limitations: list[str] = Field(default_factory=list)


class QAReviewResult(Contract):
    task_id: Annotated[str, Field(min_length=1)]
    iteration: Annotated[int, Field(ge=1)]
    reviewed_commit: Commit
    review_status: Literal["PASSED", "FAILED", "INCONCLUSIVE"]
    findings: list[ReviewFinding] = Field(default_factory=list)
    tests_executed: list[str] = Field(default_factory=list)
    evidence_refs: list[ArtifactRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_status(self) -> QAReviewResult:
        if len({finding.finding_id for finding in self.findings}) != len(self.findings):
            raise ValueError("finding IDs must be unique")
        blocking = any(
            finding.status == "REPRODUCED"
            and finding.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
            for finding in self.findings
        )
        if (self.review_status == "PASSED" and blocking) or (
            self.review_status == "FAILED" and not blocking
        ):
            raise ValueError("QA status conflicts with reproduced findings")
        return self


class ReworkRequest(Contract):
    task_id: Annotated[str, Field(min_length=1)]
    iteration: Annotated[int, Field(ge=1)]
    target_agent: Annotated[str, Field(min_length=1)]
    base_commit: Commit
    findings_to_address: list[str] = Field(min_length=1)
    required_changes: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    max_remaining_iterations: Annotated[int, Field(ge=0)]
