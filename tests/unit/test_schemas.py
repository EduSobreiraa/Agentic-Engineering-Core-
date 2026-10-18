from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aec.communication.schemas import ArtifactRef, Message, MessageType


def envelope() -> dict[str, object]:
    return {
        "protocol": "ACP",
        "version": "0.1",
        "message_id": "m1",
        "run_id": "r1",
        "task_id": "t1",
        "sender": "orchestrator",
        "recipient": "implementer",
        "type": "TASK_ASSIGNED",
        "iteration": 0,
        "created_at": datetime.now(timezone.utc),
        "payload": {"title": "Tarefa", "instructions": "Fazer"},
    }


def test_valid_message_round_trips_json() -> None:
    message = Message.model_validate(envelope())
    assert Message.model_validate_json(message.model_dump_json()) == message


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (MessageType.TASK_ASSIGNED, {"title": "T", "instructions": "I"}),
        (MessageType.REVIEW_REQUEST, {"artifact_id": "a", "criteria": "C"}),
        (
            MessageType.FINDING_REPORTED,
            {
                "artifact_id": "a",
                "severity": "high",
                "description": "D",
            },
        ),
        (
            MessageType.REWORK_REQUEST,
            {
                "finding_message_id": "f",
                "instructions": "I",
            },
        ),
        (
            MessageType.VALIDATION_RESULT,
            {
                "artifact_id": "a",
                "passed": True,
                "evidence": "E",
            },
        ),
        (MessageType.METHODOLOGY_PROPOSAL, {"title": "T", "rationale": "R"}),
        (MessageType.APPROVAL_REQUEST, {"subject": "S", "reason": "R"}),
        (
            MessageType.TASK_COMPLETED,
            {
                "summary": "S",
                "validation_message_id": "v",
            },
        ),
        (MessageType.TASK_FAILED, {"reason": "R"}),
    ],
)
def test_typed_payloads(kind: MessageType, payload: dict[str, object]) -> None:
    message = Message.model_validate(envelope() | {"type": kind, "payload": payload})
    assert message.type == kind


def test_artifact_submission_requires_reference() -> None:
    data = envelope() | {
        "type": MessageType.ARTIFACT_SUBMITTED,
        "payload": {"summary": "S", "artifact_id": "a"},
    }
    with pytest.raises(ValidationError):
        Message.model_validate(data)
    data["artifact_refs"] = [
        {
            "artifact_id": "a",
            "kind": "report",
            "uri": "artifact://r1/a",
            "sha256": "0" * 64,
            "created_by": "orchestrator",
        }
    ]
    assert Message.model_validate(data).type == MessageType.ARTIFACT_SUBMITTED


def test_large_inline_payload_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Message.model_validate(
            envelope()
            | {
                "payload": {"title": "T", "instructions": "x" * 70_000},
            }
        )


@pytest.mark.parametrize(
    "field",
    [
        "protocol",
        "version",
        "message_id",
        "run_id",
        "task_id",
        "sender",
        "recipient",
        "type",
        "iteration",
        "created_at",
        "payload",
    ],
)
def test_required_fields(field: str) -> None:
    data = envelope()
    del data[field]
    with pytest.raises(ValidationError):
        Message.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        {"version": "0.2"},
        {"protocol": "OTHER"},
        {"type": "UNKNOWN"},
        {"iteration": -1},
        {"created_at": datetime(2026, 1, 1)},
        {"payload": {"title": "Tarefa", "instructions": "Fazer", "code": "run()"}},
        {"payload": {"artifact_id": "a1", "summary": "Texto"}},
    ],
)
def test_invalid_contracts(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Message.model_validate(envelope() | change)


@pytest.mark.parametrize(
    "uri",
    [
        "file:///etc/passwd",
        "artifact://run/../secret",
        "artifact://run/%2e%2e/secret",
        "artifact://run/path?x=1",
        "artifact://run//secret",
    ],
)
def test_invalid_artifact_uri(uri: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(
            artifact_id="a",
            kind="report",
            uri=uri,
            sha256="0" * 64,
            created_by="qa-adversarial",
        )
