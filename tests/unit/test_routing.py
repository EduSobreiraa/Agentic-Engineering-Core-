from aec.communication.schemas import MessageType
from aec.security.identities import Role
from aec.security.permissions import ALLOWED_RECIPIENTS, ALLOWED_SENDERS


def test_every_message_type_has_explicit_route() -> None:
    assert set(ALLOWED_SENDERS) == set(ALLOWED_RECIPIENTS) == set(MessageType)
    assert ALLOWED_SENDERS[MessageType.REWORK_REQUEST] == {Role.ORCHESTRATOR}
    assert ALLOWED_SENDERS[MessageType.METHODOLOGY_PROPOSAL] == {Role.METHODOLOGY}
