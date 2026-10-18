from datetime import datetime, timezone
from uuid import uuid4

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import Message, MessageType
from aec.security.identities import Identity, Role


@pytest.fixture
def setup_broker(tmp_path):  # type: ignore[no-untyped-def]
    broker = Broker(tmp_path / "acp.sqlite", lease_seconds=5, max_attempts=2)
    identities = {role: broker.register(role.value, role) for role in Role}
    return broker, identities


def make_message(
    sender: Identity,
    recipient: Identity,
    kind: MessageType = MessageType.TASK_ASSIGNED,
    payload: dict[str, object] | None = None,
    *,
    message_id: str | None = None,
) -> Message:
    return Message.model_validate(
        {
            "protocol": "ACP",
            "version": "0.1",
            "message_id": message_id or str(uuid4()),
            "run_id": "run-1",
            "task_id": "task-1",
            "sender": sender.agent_id,
            "recipient": recipient.agent_id,
            "type": kind,
            "iteration": 0,
            "created_at": datetime.now(timezone.utc),
            "payload": payload or {"title": "Tarefa", "instructions": "Executar"},
        }
    )
