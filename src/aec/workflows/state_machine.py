"""Allowed lifecycle transitions."""

from .models import StepStatus, WorkflowStatus

WORKFLOW_TRANSITIONS = {
    WorkflowStatus.CREATED: {WorkflowStatus.VALIDATED, WorkflowStatus.CANCELLED},
    WorkflowStatus.VALIDATED: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.RUNNING: {
        WorkflowStatus.WAITING_APPROVAL,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.INTERRUPTED,
    },
    WorkflowStatus.WAITING_APPROVAL: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.INTERRUPTED: {WorkflowStatus.CANCELLED},
}
STEP_TRANSITIONS = {
    StepStatus.PENDING: {StepStatus.READY, StepStatus.SKIPPED, StepStatus.CANCELLED},
    StepStatus.READY: {
        StepStatus.RUNNING,
        StepStatus.WAITING_APPROVAL,
        StepStatus.CANCELLED,
    },
    StepStatus.RUNNING: {
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.INTERRUPTED,
        StepStatus.CANCELLED,
        StepStatus.READY,
    },
    StepStatus.WAITING_APPROVAL: {
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    },
}


def check_transition(
    old: WorkflowStatus | StepStatus, new: WorkflowStatus | StepStatus
) -> None:
    if isinstance(old, WorkflowStatus) and isinstance(new, WorkflowStatus):
        if new not in WORKFLOW_TRANSITIONS.get(old, set()):
            raise ValueError(f"illegal transition: {old} -> {new}")
    elif isinstance(old, StepStatus) and isinstance(new, StepStatus):
        if new not in STEP_TRANSITIONS.get(old, set()):
            raise ValueError(f"illegal transition: {old} -> {new}")
    else:
        raise ValueError("mixed workflow and step states")
