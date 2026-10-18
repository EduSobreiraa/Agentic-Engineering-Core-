from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from aec.methodology.contracts import (
    Change,
    MethodologyObservation,
    MethodologyProposal,
)
from aec.methodology.metrics import calculate

MOMENT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def observation(
    run_id: str,
    *,
    started_at: datetime = MOMENT,
    iterations: int | None = 1,
    findings: tuple[str, ...] = (),
    gates: bool | None = True,
) -> MethodologyObservation:
    return MethodologyObservation(
        run_id=run_id,
        task_id=run_id,
        methodology_id="dev-adversarial",
        version="1.0.0",
        started_at=started_at,
        finished_at=None,
        status="APPROVED",
        review_iterations=iterations,
        first_review_passed=True if iterations is not None else None,
        required_rework=False if iterations is not None else None,
        findings=findings,
        gates_passed=gates,
        human_interventions=1,
        agent_invocations=2,
        approval_at=started_at + timedelta(seconds=60),
    )


def test_empty_and_missing_metrics_are_not_zero() -> None:
    values = {item.name: item for item in calculate([], "dev-adversarial", "1.0.0")}
    assert len(values) == 10
    assert all(item.value is None for item in values.values())
    assert values["documentation_completeness"].missing is not None
    partial = {
        item.name: item
        for item in calculate(
            [observation("a", iterations=None, gates=None)],
            "dev-adversarial",
            "1.0.0",
        )
    }
    assert partial["validation_success_rate"].value is None
    assert partial["average_review_iterations"].value is None
    assert partial["workflow_completion_rate"].value == 1


def test_window_and_recurrence() -> None:
    runs = [
        observation("a", findings=("typing",)),
        observation("b", findings=("typing",), started_at=MOMENT + timedelta(days=1)),
        observation("c", findings=("security",), started_at=MOMENT + timedelta(days=2)),
    ]
    all_values = {m.name: m.value for m in calculate(runs, "dev-adversarial", "1.0.0")}
    assert all_values["defect_recurrence_rate"] == pytest.approx(1 / 3)
    day_one = {
        m.name: m.value
        for m in calculate(
            runs,
            "dev-adversarial",
            "1.0.0",
            start=MOMENT,
            end=MOMENT + timedelta(days=1),
        )
    }
    assert day_one["defect_recurrence_rate"] == 0
    with pytest.raises(ValueError, match="end must follow"):
        calculate(runs, "dev-adversarial", "1.0.0", start=MOMENT, end=MOMENT)


def test_documentation_completeness_uses_declared_denominator() -> None:
    runs = [
        observation("documented").model_copy(
            update={"required_documents": 3, "valid_documents": 2}
        ),
        observation("missing").model_copy(
            update={"required_documents": 1, "valid_documents": 0}
        ),
        observation("unknown"),
    ]
    values = {
        item.name: item
        for item in calculate(runs, "dev-adversarial", "1.0.0")
    }
    completeness = values["documentation_completeness"]
    assert (completeness.numerator, completeness.denominator, completeness.value) == (
        2,
        4,
        0.5,
    )


def test_proposal_rejects_arbitrary_operation_and_version() -> None:
    valid = dict(
        proposal_id="RFC-1",
        methodology_id="dev-adversarial",
        base_version="1.0.0",
        proposed_version="1.1.0",
        problem="typing",
        hypothesis="type check helps",
        evidence_refs=("run-1",),
        changes=(
            Change(
                operation="add_quality_gate", gate="type_check", position="before_qa"
            ),
        ),
        expected_metric="average_review_iterations",
        expected_direction="decrease",
        risks="delay",
        validation_criteria="paired tasks",
        rollback_plan="use 1.0.0",
    )
    assert MethodologyProposal.model_validate(valid).proposed_version == "1.1.0"
    with pytest.raises(ValidationError):
        MethodologyProposal.model_validate({**valid, "proposed_version": "1.0.0"})
    with pytest.raises(ValidationError):
        Change.model_validate(
            {"operation": "run_shell", "gate": "type_check", "position": "before_qa"}
        )
    with pytest.raises(ValidationError):
        MethodologyProposal.model_validate(
            {**valid, "evidence_refs": ("run-1", "run-1")}
        )
    with pytest.raises(ValidationError):
        MethodologyProposal.model_validate({**valid, "proposal_id": "../outside"})
