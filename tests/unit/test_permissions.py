import sqlite3

import pytest
from pydantic import ValidationError

from aec.communication.errors import AuthorizationError
from aec.communication.schemas import MessageType
from aec.security.identities import Role
from tests.conftest import make_message


def test_authorized_publication(setup_broker) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    message = make_message(ids[Role.ORCHESTRATOR], ids[Role.IMPLEMENTER])
    assert broker.publish(message, ids[Role.ORCHESTRATOR]) == message


def test_sender_spoof_and_role_denial_are_audited(setup_broker) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    message = make_message(ids[Role.ORCHESTRATOR], ids[Role.IMPLEMENTER])
    with pytest.raises(AuthorizationError):
        broker.publish(message, ids[Role.QA])
    forged = make_message(ids[Role.QA], ids[Role.IMPLEMENTER])
    with pytest.raises(AuthorizationError):
        broker.publish(forged, ids[Role.QA])
    assert broker.get_message(message.message_id) is None
    with sqlite3.connect(broker.db.path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM events WHERE kind = 'PUBLISH_REJECTED'"
            ).fetchone()[0]
            == 2
        )


def test_completion_and_recipient_policy(setup_broker) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    unauthorized = make_message(
        ids[Role.IMPLEMENTER],
        ids[Role.QUALITY_GATE],
        MessageType.TASK_COMPLETED,
        {"summary": "Pronto", "validation_message_id": "v1"},
    )
    with pytest.raises(AuthorizationError):
        broker.publish(unauthorized, ids[Role.IMPLEMENTER])
    wrong_recipient = make_message(ids[Role.ORCHESTRATOR], ids[Role.QA])
    with pytest.raises(AuthorizationError):
        broker.publish(wrong_recipient, ids[Role.ORCHESTRATOR])


def test_message_text_is_never_executed(setup_broker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    marker = tmp_path / "executed"
    command = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    message = make_message(
        ids[Role.ORCHESTRATOR],
        ids[Role.IMPLEMENTER],
        payload={"title": "Texto", "instructions": command},
    )
    broker.publish(message, ids[Role.ORCHESTRATOR])
    delivery = broker.claim(ids[Role.IMPLEMENTER].agent_id, ids[Role.IMPLEMENTER])
    assert delivery is not None and delivery.message.payload.instructions == command
    assert not marker.exists()


def test_broker_revalidates_mutated_model(setup_broker) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    valid = make_message(ids[Role.ORCHESTRATOR], ids[Role.IMPLEMENTER])
    bypassed = valid.model_copy(update={"payload": {"code": "run()"}})
    with pytest.raises(ValidationError):
        broker.publish(bypassed, ids[Role.ORCHESTRATOR])
    assert broker.get_message(valid.message_id) is None
