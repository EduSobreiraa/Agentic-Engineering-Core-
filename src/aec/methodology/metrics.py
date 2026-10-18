"""Deterministic metrics over engineering observations."""

from __future__ import annotations

from datetime import datetime

from .contracts import MethodologyMetric, MethodologyObservation


def calculate(
    observations: list[MethodologyObservation],
    methodology_id: str,
    version: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[MethodologyMetric]:
    if any(bound is not None and bound.utcoffset() is None for bound in (start, end)):
        raise ValueError("metric window must include a timezone")
    if start is not None and end is not None and start >= end:
        raise ValueError("metric window end must follow start")
    runs = [
        o
        for o in observations
        if o.methodology_id == methodology_id
        and o.version == version
        and (start is None or o.started_at >= start)
        and (end is None or o.started_at < end)
    ]
    if any(o.approval_at is not None and o.approval_at < o.started_at for o in runs):
        raise ValueError("approval precedes workflow start")

    def metric(
        name: str,
        numerator: float | None,
        denominator: float | None,
        unit: str,
        source: str,
        limitation: str,
    ) -> MethodologyMetric:
        return MethodologyMetric(
            name=name,
            numerator=numerator,
            denominator=denominator,
            value=numerator / denominator
            if numerator is not None and denominator
            else None,
            unit=unit,
            window_start=start,
            window_end=end,
            methodology_id=methodology_id,
            version=version,
            source=source,
            missing="no eligible observations" if not denominator else None,
            limitation=limitation,
        )

    reviewed = [o for o in runs if o.review_iterations is not None]
    gated = [o for o in runs if o.gates_passed is not None]
    approved = [o for o in runs if o.approval_at is not None]
    approved_reviewed = [o for o in approved if o.first_review_passed is not None]
    classified = [o for o in runs if o.findings]
    seen: set[str] = set()
    recurring = 0
    for observation in classified:
        for category in observation.findings:
            recurring += category in seen
            seen.add(category)
    findings = sum(len(o.findings) for o in classified)
    return [
        metric(
            "first_pass_approval_rate",
            sum(o.first_review_passed is True for o in approved_reviewed),
            len(approved_reviewed),
            "fraction",
            "engineering_iterations",
            "Only approved runs count.",
        ),
        metric(
            "average_review_iterations",
            sum(o.review_iterations or 0 for o in reviewed),
            len(reviewed),
            "iterations/task",
            "engineering_iterations",
            "Incomplete reviews are excluded.",
        ),
        metric(
            "rework_rate",
            sum(o.required_rework is True for o in reviewed),
            len(reviewed),
            "fraction",
            "engineering_iterations",
            "A second review is a proxy for rework.",
        ),
        metric(
            "validation_success_rate",
            sum(o.gates_passed is True for o in gated),
            len(gated),
            "fraction",
            "engineering_iterations.gates",
            "Only recorded gate sets count.",
        ),
        metric(
            "workflow_completion_rate",
            sum(o.status == "APPROVED" for o in runs),
            len(runs),
            "fraction",
            "engineering_runs",
            "Approval is the terminal successful state.",
        ),
        metric(
            "human_intervention_rate",
            sum(o.human_interventions or 0 for o in runs),
            len(runs),
            "decisions/task",
            "workflow_approvals",
            "Local approval decisions only.",
        ),
        metric(
            "time_to_approval",
            sum(
                (o.approval_at - o.started_at).total_seconds()
                for o in approved
                if o.approval_at
            ),
            len(approved),
            "seconds/task",
            "workflow_approvals",
            "Only approved runs count.",
        ),
        metric(
            "documentation_completeness",
            None,
            None,
            "fraction",
            "documentation",
            "No required-document manifest exists in Phase 5.",
        ),
        metric(
            "agent_invocation_count",
            sum(o.agent_invocations or 0 for o in runs),
            len(runs),
            "invocations/task",
            "executions",
            "Counts persisted execution records.",
        ),
        metric(
            "defect_recurrence_rate",
            recurring,
            findings,
            "fraction",
            "engineering_iterations.review",
            "Category repetition within observed reviews; classification may vary.",
        ),
    ]
