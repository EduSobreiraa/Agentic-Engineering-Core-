from datetime import datetime, timedelta, timezone
from pathlib import Path

from aec.communication.broker import Broker
from aec.security.identities import Role
from tests.conftest import make_message


def test_expired_final_attempt_goes_to_dead_letter(tmp_path: Path) -> None:
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    broker = Broker(
        tmp_path / "acp.sqlite", lease_seconds=1, max_attempts=1, clock=lambda: now[0]
    )
    sender = broker.register("orchestrator", Role.ORCHESTRATOR)
    recipient = broker.register("implementer", Role.IMPLEMENTER)
    broker.publish(make_message(sender, recipient), sender)
    assert broker.claim(recipient.agent_id, recipient) is not None
    now[0] += timedelta(seconds=2)
    assert broker.recover_expired_deliveries() == 1
    assert broker.claim(recipient.agent_id, recipient) is None
