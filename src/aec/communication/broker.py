"""Transactional local ACP broker."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from aec.persistence.database import Database
from aec.security.identities import Identity, Role
from aec.security.permissions import ALLOWED_RECIPIENTS, ALLOWED_SENDERS

from .errors import AuthorizationError, ConflictError, StateError
from .schemas import Message


@dataclass(frozen=True)
class ClaimedDelivery:
    delivery_id: str
    claim_token: str
    attempts: int
    message: Message


class Broker:
    def __init__(
        self,
        database_path: str | Path,
        *,
        lease_seconds: int = 30,
        max_attempts: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("lease_seconds and max_attempts must be positive")
        self.db = Database(database_path)
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._handles: dict[str, Identity] = {}

    def _now(self) -> str:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("broker clock must be timezone aware")
        return now.astimezone(timezone.utc).isoformat()

    def _identity(self, identity: Identity) -> Identity:
        known = self._handles.get(identity.handle)
        if known != identity:
            raise AuthorizationError("invalid local identity handle")
        return known

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        kind: str,
        now: str,
        *,
        message_id: str | None = None,
        agent_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        db.execute(
            "INSERT INTO events(message_id, agent_id, kind, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, agent_id, kind, detail, now),
        )

    def register(self, agent_id: str, role: Role) -> Identity:
        if not agent_id:
            raise ValueError("agent_id is required")
        now = self._now()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT role FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO agents VALUES (?, ?, ?)", (agent_id, role.value, now)
                )
                self._event(db, "AGENT_REGISTERED", now, agent_id=agent_id)
            elif row["role"] != role.value:
                raise ConflictError("agent_id already has another role")
        identity = Identity(agent_id, role, secrets.token_urlsafe(32))
        self._handles[identity.handle] = identity
        return identity

    def publish(self, message: Message, identity: Identity) -> Message:
        message = Message.model_validate(
            message.model_dump(mode="json", warnings=False)
        )
        actor = self._identity(identity)
        now = self._now()
        conflict = False
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recipient = db.execute(
                "SELECT role FROM agents WHERE agent_id = ?", (message.recipient,)
            ).fetchone()
            reason = None
            if actor.agent_id != message.sender:
                reason = "sender does not match identity"
            elif actor.role not in ALLOWED_SENDERS[message.type]:
                reason = "sender role cannot publish this type"
            elif (
                recipient is None
                or Role(recipient["role"]) not in ALLOWED_RECIPIENTS[message.type]
            ):
                reason = "recipient is not allowed for this type"
            if reason:
                self._event(
                    db,
                    "PUBLISH_REJECTED",
                    now,
                    message_id=message.message_id,
                    agent_id=actor.agent_id,
                    detail=reason,
                )
            else:
                content = message.model_dump_json()
                row = db.execute(
                    "SELECT content FROM messages WHERE message_id = ?",
                    (message.message_id,),
                ).fetchone()
                if row is not None:
                    if row["content"] != content:
                        self._event(
                            db,
                            "PUBLISH_CONFLICT",
                            now,
                            message_id=message.message_id,
                            agent_id=actor.agent_id,
                        )
                        conflict = True
                    else:
                        return Message.model_validate_json(row["content"])
                else:
                    db.execute(
                        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            message.message_id,
                            message.run_id,
                            message.task_id,
                            message.sender,
                            message.recipient,
                            message.type.value,
                            content,
                            message.created_at.astimezone(timezone.utc).isoformat(),
                        ),
                    )
                    db.execute(
                        "INSERT INTO deliveries"
                        "(delivery_id, message_id, recipient, state) "
                        "VALUES (?, ?, ?, 'PENDING')",
                        (secrets.token_hex(16), message.message_id, message.recipient),
                    )
                    self._event(
                        db,
                        "PUBLISHED",
                        now,
                        message_id=message.message_id,
                        agent_id=actor.agent_id,
                    )
        if reason:
            raise AuthorizationError(reason)
        if conflict:
            raise ConflictError("message_id already names different content")
        return message

    def claim(self, recipient: str, identity: Identity) -> ClaimedDelivery | None:
        actor = self._identity(identity)
        if actor.agent_id != recipient:
            raise AuthorizationError("recipient does not match identity")
        now = self._now()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover(db, now)
            row = db.execute(
                "SELECT d.delivery_id, d.attempts, m.content FROM deliveries d "
                "JOIN messages m ON m.message_id = d.message_id "
                "WHERE d.recipient = ? AND d.state IN ('PENDING', 'RETRY_PENDING') "
                "ORDER BY m.created_at, m.message_id LIMIT 1",
                (recipient,),
            ).fetchone()
            if row is None:
                return None
            token = secrets.token_urlsafe(32)
            until = (
                datetime.fromisoformat(now) + timedelta(seconds=self.lease_seconds)
            ).isoformat()
            db.execute(
                "UPDATE deliveries SET state = 'CLAIMED', attempts = attempts + 1, "
                "claim_token = ?, lease_until = ? WHERE delivery_id = ?",
                (token, until, row["delivery_id"]),
            )
            message = Message.model_validate_json(row["content"])
            self._event(
                db,
                "CLAIMED",
                now,
                message_id=message.message_id,
                agent_id=actor.agent_id,
                detail=str(row["attempts"] + 1),
            )
            return ClaimedDelivery(
                row["delivery_id"], token, row["attempts"] + 1, message
            )

    def ack(self, delivery: ClaimedDelivery, identity: Identity) -> None:
        actor = self._identity(identity)
        now = self._now()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT message_id, recipient, state, claim_token, lease_until "
                "FROM deliveries WHERE delivery_id = ?",
                (delivery.delivery_id,),
            ).fetchone()
            if row is None or row["recipient"] != actor.agent_id:
                raise AuthorizationError("delivery does not belong to identity")
            if row["claim_token"] != delivery.claim_token:
                raise StateError("stale claim token")
            if row["state"] == "ACKNOWLEDGED":
                return
            if row["state"] != "CLAIMED" or row["lease_until"] <= now:
                raise StateError("claim is no longer active")
            db.execute(
                "UPDATE deliveries SET state = 'ACKNOWLEDGED', acknowledged_at = ?, "
                "lease_until = NULL WHERE delivery_id = ?",
                (now, delivery.delivery_id),
            )
            db.execute(
                "INSERT OR IGNORE INTO processing_receipts VALUES (?, ?, ?)",
                (row["message_id"], actor.agent_id, now),
            )
            self._event(
                db,
                "ACKNOWLEDGED",
                now,
                message_id=row["message_id"],
                agent_id=actor.agent_id,
            )

    def nack(
        self,
        delivery: ClaimedDelivery,
        reason: str,
        identity: Identity,
        *,
        permanent: bool = False,
    ) -> None:
        actor = self._identity(identity)
        if not reason:
            raise ValueError("reason is required")
        now = self._now()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT message_id, recipient, state, claim_token, "
                "lease_until, attempts "
                "FROM deliveries WHERE delivery_id = ?",
                (delivery.delivery_id,),
            ).fetchone()
            if row is None or row["recipient"] != actor.agent_id:
                raise AuthorizationError("delivery does not belong to identity")
            if (
                row["state"] != "CLAIMED"
                or row["claim_token"] != delivery.claim_token
                or row["lease_until"] <= now
            ):
                raise StateError("claim is no longer active")
            state = (
                "DEAD_LETTER"
                if permanent or row["attempts"] >= self.max_attempts
                else "RETRY_PENDING"
            )
            db.execute(
                "UPDATE deliveries SET state = ?, claim_token = NULL, "
                "lease_until = NULL, "
                "last_error = ? WHERE delivery_id = ?",
                (state, reason, delivery.delivery_id),
            )
            self._event(
                db,
                state,
                now,
                message_id=row["message_id"],
                agent_id=actor.agent_id,
                detail=reason,
            )

    def _recover(self, db: sqlite3.Connection, now: str) -> int:
        rows = db.execute(
            "SELECT message_id, delivery_id, attempts, recipient FROM deliveries "
            "WHERE state = 'CLAIMED' AND lease_until <= ?",
            (now,),
        ).fetchall()
        for row in rows:
            state = (
                "DEAD_LETTER"
                if row["attempts"] >= self.max_attempts
                else "RETRY_PENDING"
            )
            db.execute(
                "UPDATE deliveries SET state = ?, claim_token = NULL, "
                "lease_until = NULL, "
                "last_error = 'lease expired' WHERE delivery_id = ?",
                (state, row["delivery_id"]),
            )
            self._event(
                db,
                state,
                now,
                message_id=row["message_id"],
                agent_id=row["recipient"],
                detail="lease expired",
            )
        return len(rows)

    def recover_expired_deliveries(self) -> int:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._recover(db, self._now())

    def get_message(self, message_id: str) -> Message | None:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT content FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return Message.model_validate_json(row["content"]) if row else None

    def get_history(self, run_id: str) -> list[Message]:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT content FROM messages WHERE run_id = ? "
                "ORDER BY created_at, message_id",
                (run_id,),
            ).fetchall()
        return [Message.model_validate_json(row["content"]) for row in rows]
