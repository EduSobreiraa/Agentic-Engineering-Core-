"""Execution and session state in the ACP SQLite database."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256

from aec.persistence.database import Database

from .models import AgentSession, ExecutionRequest, ExecutionResult, ExecutionStatus


class ExecutionRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    def reconcile(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as db:
            cursor = db.execute(
                "UPDATE executions SET status = ?, updated_at = ? "
                "WHERE status IN (?, ?)",
                (
                    ExecutionStatus.INTERRUPTED,
                    now,
                    ExecutionStatus.STARTING,
                    ExecutionStatus.RUNNING,
                ),
            )
            return cursor.rowcount

    def create(self, request: ExecutionRequest, source_message_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request_hash, source_message_id FROM executions "
                "WHERE execution_id = ?",
                (request.execution_id,),
            ).fetchone()
            expected = (
                request.run_id,
                request.task_id,
                request.agent_id,
                request.executor,
                str(request.working_directory),
            )
            digest = sha256(request.model_dump_json().encode()).hexdigest()
            if row:
                if (
                    row["request_hash"] != digest
                    or row["source_message_id"] != source_message_id
                ):
                    raise ValueError("execution_id already names another request")
                return False
            source_row = db.execute(
                "SELECT execution_id FROM executions WHERE source_message_id = ?",
                (source_message_id,),
            ).fetchone()
            if source_row:
                raise ValueError("ACP message already has an execution")
            db.execute(
                "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    request.execution_id,
                    *expected,
                    source_message_id,
                    digest,
                    ExecutionStatus.CREATED,
                    now,
                    now,
                ),
            )
            return True

    def status(self, execution_id: str, status: ExecutionStatus) -> None:
        with self.db.connect() as db:
            db.execute(
                "UPDATE executions SET status = ?, updated_at = ? "
                "WHERE execution_id = ?",
                (status, datetime.now(timezone.utc).isoformat(), execution_id),
            )

    def finish(self, result: ExecutionResult) -> None:
        with self.db.connect() as db:
            db.execute(
                "UPDATE executions SET status = ?, result = ?, updated_at = ? "
                "WHERE execution_id = ?",
                (
                    result.status,
                    result.model_dump_json(),
                    result.finished_at.isoformat(),
                    result.execution_id,
                ),
            )

    def get(self, execution_id: str) -> ExecutionResult | ExecutionStatus | None:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT status, result FROM executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        return (
            ExecutionResult.model_validate_json(row["result"])
            if row["result"]
            else ExecutionStatus(row["status"])
        )

    def save_session(self, session: AgentSession) -> None:
        with self.db.connect() as db:
            db.execute(
                "INSERT INTO agent_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET state=excluded.state, "
                "last_used_at=excluded.last_used_at",
                (
                    session.session_id,
                    session.provider_session_id,
                    session.executor,
                    session.agent_id,
                    str(session.working_directory),
                    session.state,
                    session.created_at.isoformat(),
                    session.last_used_at.isoformat(),
                ),
            )

    def get_session(self, session_id: str) -> AgentSession | None:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return AgentSession.model_validate_json(json.dumps(dict(row))) if row else None
