"""ACP publication and routing policy."""

from aec.communication.schemas import MessageType

from .identities import Role

ALLOWED_SENDERS: dict[MessageType, frozenset[Role]] = {
    MessageType.TASK_ASSIGNED: frozenset({Role.ORCHESTRATOR}),
    MessageType.ARTIFACT_SUBMITTED: frozenset({Role.IMPLEMENTER, Role.DOCUMENTATION}),
    MessageType.REVIEW_REQUEST: frozenset({Role.ORCHESTRATOR}),
    MessageType.FINDING_REPORTED: frozenset({Role.QA}),
    MessageType.REWORK_REQUEST: frozenset({Role.ORCHESTRATOR}),
    MessageType.VALIDATION_RESULT: frozenset({Role.QA, Role.QUALITY_GATE}),
    MessageType.METHODOLOGY_PROPOSAL: frozenset({Role.METHODOLOGY}),
    MessageType.APPROVAL_REQUEST: frozenset({Role.ORCHESTRATOR}),
    MessageType.TASK_COMPLETED: frozenset({Role.ORCHESTRATOR}),
    MessageType.TASK_FAILED: frozenset({Role.ORCHESTRATOR}),
    MessageType.DOCUMENTATION_REQUEST: frozenset({Role.ORCHESTRATOR}),
    MessageType.DOCUMENTATION_GENERATED: frozenset({Role.DOCUMENTATION}),
    MessageType.DOCUMENTATION_FAILED: frozenset({Role.DOCUMENTATION}),
}

ALLOWED_RECIPIENTS: dict[MessageType, frozenset[Role]] = {
    MessageType.TASK_ASSIGNED: frozenset({Role.IMPLEMENTER}),
    MessageType.ARTIFACT_SUBMITTED: frozenset({Role.ORCHESTRATOR}),
    MessageType.REVIEW_REQUEST: frozenset({Role.QA}),
    MessageType.FINDING_REPORTED: frozenset({Role.ORCHESTRATOR}),
    MessageType.REWORK_REQUEST: frozenset({Role.IMPLEMENTER}),
    MessageType.VALIDATION_RESULT: frozenset({Role.ORCHESTRATOR}),
    MessageType.METHODOLOGY_PROPOSAL: frozenset({Role.ORCHESTRATOR}),
    MessageType.APPROVAL_REQUEST: frozenset({Role.ORCHESTRATOR}),
    MessageType.TASK_COMPLETED: frozenset({Role.QUALITY_GATE}),
    MessageType.TASK_FAILED: frozenset({Role.QUALITY_GATE}),
    MessageType.DOCUMENTATION_REQUEST: frozenset({Role.DOCUMENTATION}),
    MessageType.DOCUMENTATION_GENERATED: frozenset({Role.ORCHESTRATOR}),
    MessageType.DOCUMENTATION_FAILED: frozenset({Role.ORCHESTRATOR}),
}
