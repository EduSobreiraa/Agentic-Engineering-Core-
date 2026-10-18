from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aec.communication.broker import Broker
from aec.communication.errors import StateError
from aec.security.identities import Role
from tests.conftest import make_message


def test_claim_ack_and_duplicate_ack(setup_broker) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    message = make_message(ids[Role.ORCHESTRATOR], ids[Role.IMPLEMENTER])
    broker.publish(message, ids[Role.ORCHESTRATOR])
    delivery = broker.claim(ids[Role.IMPLEMENTER].agent_id, ids[Role.IMPLEMENTER])
    assert (
        delivery is not None and delivery.message == message and delivery.attempts == 1
    )
    broker.ack(delivery, ids[Role.IMPLEMENTER])
    broker.ack(delivery, ids[Role.IMPLEMENTER])
    assert broker.claim(ids[Role.IMPLEMENTER].agent_id, ids[Role.IMPLEMENTER]) is None


def test_nack_retry_and_dead_letter(setup_broker) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    broker.publish(
        make_message(ids[Role.ORCHESTRATOR], ids[Role.IMPLEMENTER]),
        ids[Role.ORCHESTRATOR],
    )
    first = broker.claim(ids[Role.IMPLEMENTER].agent_id, ids[Role.IMPLEMENTER])
    assert first is not None
    broker.nack(first, "transient", ids[Role.IMPLEMENTER])
    second = broker.claim(ids[Role.IMPLEMENTER].agent_id, ids[Role.IMPLEMENTER])
    assert second is not None and second.attempts == 2
    with pytest.raises(StateError):
        broker.ack(first, ids[Role.IMPLEMENTER])
    broker.nack(second, "still broken", ids[Role.IMPLEMENTER])
    assert broker.claim(ids[Role.IMPLEMENTER].agent_id, ids[Role.IMPLEMENTER]) is None


def test_expired_lease_recovers_after_restart(tmp_path: Path) -> None:
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    path = tmp_path / "acp.sqlite"
    first_broker = Broker(path, lease_seconds=5, clock=lambda: now[0])
    sender = first_broker.register("orchestrator", Role.ORCHESTRATOR)
    worker = first_broker.register("implementer", Role.IMPLEMENTER)
    message = make_message(sender, worker)
    first_broker.publish(message, sender)
    old_claim = first_broker.claim(worker.agent_id, worker)
    assert old_claim is not None
    now[0] += timedelta(seconds=6)
    second_broker = Broker(path, lease_seconds=5, clock=lambda: now[0])
    worker2 = second_broker.register("implementer", Role.IMPLEMENTER)
    assert second_broker.recover_expired_deliveries() == 1
    new_claim = second_broker.claim(worker2.agent_id, worker2)
    assert new_claim is not None and new_claim.message == message
    assert new_claim.attempts == 2
    second_broker.ack(new_claim, worker2)
    with pytest.raises(StateError):
        first_broker.ack(old_claim, worker)


def test_two_consumers_claim_once(tmp_path: Path) -> None:
    path = tmp_path / "acp.sqlite"
    broker = Broker(path)
    sender = broker.register("orchestrator", Role.ORCHESTRATOR)
    recipient = broker.register("implementer", Role.IMPLEMENTER)
    broker.publish(make_message(sender, recipient), sender)
    consumers = [Broker(path), Broker(path)]
    identities = [
        consumer.register("implementer", Role.IMPLEMENTER) for consumer in consumers
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda pair: pair[0].claim("implementer", pair[1]),
                zip(consumers, identities),
            )
        )
    assert sum(claim is not None for claim in claims) == 1
