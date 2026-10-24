import sqlite3
from pathlib import Path

import pytest

from aec.communication.broker import Broker
from aec.communication.errors import ConflictError
from aec.persistence.database import Database
from aec.persistence.migrations import MIGRATIONS
from aec.security.identities import Role
from tests.conftest import make_message


def test_phase_five_database_upgrades_without_losing_document_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "phase-five.sqlite"
    with sqlite3.connect(path) as connection:
        for index, migration in enumerate(MIGRATIONS[:6], start=1):
            connection.executescript(migration)
            connection.execute(f"PRAGMA user_version = {index}")
    db = Database(path)
    with db.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == len(
            MIGRATIONS
        )
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"documentation_records", "adr_records", "methodology_proposals"} <= names


def test_atomic_publish_history_and_receipt(setup_broker) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    message = make_message(ids[Role.ORCHESTRATOR], ids[Role.IMPLEMENTER])
    broker.publish(message, ids[Role.ORCHESTRATOR])
    assert broker.get_message(message.message_id) == message
    assert broker.get_history("run-1") == [message]
    with sqlite3.connect(broker.db.path) as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert db.execute("SELECT state FROM deliveries").fetchone()[0] == "PENDING"
        assert (
            db.execute(
                "SELECT COUNT(*) FROM events WHERE kind = 'PUBLISHED'"
            ).fetchone()[0]
            == 1
        )
    delivery = broker.claim(ids[Role.IMPLEMENTER].agent_id, ids[Role.IMPLEMENTER])
    assert delivery is not None
    broker.ack(delivery, ids[Role.IMPLEMENTER])
    broker.ack(delivery, ids[Role.IMPLEMENTER])
    with sqlite3.connect(broker.db.path) as db:
        assert db.execute("SELECT COUNT(*) FROM processing_receipts").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM events WHERE kind = 'ACKNOWLEDGED'"
            ).fetchone()[0]
            == 1
        )


def test_republish_is_idempotent_and_conflict_is_recorded(setup_broker) -> None:  # type: ignore[no-untyped-def]
    broker, ids = setup_broker
    message = make_message(ids[Role.ORCHESTRATOR], ids[Role.IMPLEMENTER])
    broker.publish(message, ids[Role.ORCHESTRATOR])
    broker.publish(message, ids[Role.ORCHESTRATOR])
    changed = message.model_copy(update={"task_id": "other-task"})
    with pytest.raises(ConflictError):
        broker.publish(changed, ids[Role.ORCHESTRATOR])
    with sqlite3.connect(broker.db.path) as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM events WHERE kind = 'PUBLISH_CONFLICT'"
            ).fetchone()[0]
            == 1
        )


def test_restart_keeps_pending_and_history(tmp_path: Path) -> None:
    path = tmp_path / "acp.sqlite"
    first = Broker(path)
    sender = first.register("orchestrator", Role.ORCHESTRATOR)
    recipient = first.register("implementer", Role.IMPLEMENTER)
    message = make_message(sender, recipient)
    first.publish(message, sender)
    second = Broker(path)
    resumed = second.register("implementer", Role.IMPLEMENTER)
    assert second.get_history("run-1") == [message]
    delivery = second.claim(resumed.agent_id, resumed)
    assert delivery is not None and delivery.message == message
    second.ack(delivery, resumed)
