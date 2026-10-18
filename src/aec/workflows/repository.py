"""Persistent workflow state in the shared SQLite database."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from aec.persistence.database import Database

from .loader import definition_hash
from .models import StepRun, StepStatus, WorkflowDefinition, WorkflowRun, WorkflowStatus
from .state_machine import check_transition


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, run_id: str, definition: WorkflowDefinition) -> WorkflowRun:
        created = now()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)",
                (
                    run_id,
                    definition.model_dump_json(),
                    definition_hash(definition),
                    WorkflowStatus.CREATED,
                    created,
                ),
            )
            db.executemany(
                "INSERT INTO workflow_steps(run_id, step_id, status) VALUES (?, ?, ?)",
                [(run_id, step.id, StepStatus.PENDING) for step in definition.steps],
            )
            self._event(db, run_id, None, "CREATED", None)
        return self.get_run(run_id)

    def definition(self, run_id: str) -> WorkflowDefinition:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT definition, definition_hash FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        definition = WorkflowDefinition.model_validate_json(row["definition"])
        if definition_hash(definition) != row["definition_hash"]:
            raise RuntimeError("stored workflow definition hash mismatch")
        return definition

    def get_run(self, run_id: str) -> WorkflowRun:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        definition = self.definition(run_id)
        return WorkflowRun(
            run_id=run_id,
            workflow_id=definition.id,
            workflow_version=definition.version,
            definition_hash=row["definition_hash"],
            status=WorkflowStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"])
            if row["started_at"]
            else None,
            finished_at=datetime.fromisoformat(row["finished_at"])
            if row["finished_at"]
            else None,
            error=row["error"],
        )

    def steps(self, run_id: str) -> dict[str, StepRun]:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_steps WHERE run_id=?", (run_id,)
            ).fetchall()
        return {
            row["step_id"]: StepRun(
                step_run_id=f"{run_id}:{row['step_id']}",
                run_id=run_id,
                step_id=row["step_id"],
                status=StepStatus(row["status"]),
                attempt=row["attempt"],
                execution_id=row["execution_id"],
                started_at=datetime.fromisoformat(row["started_at"])
                if row["started_at"]
                else None,
                finished_at=datetime.fromisoformat(row["finished_at"])
                if row["finished_at"]
                else None,
                result=json.loads(row["result"]) if row["result"] else None,
            )
            for row in rows
        }

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        run_id: str,
        step_id: str | None,
        kind: str,
        detail: str | None,
    ) -> None:
        db.execute(
            "INSERT INTO workflow_events(run_id,step_id,kind,detail,created_at) "
            "VALUES (?,?,?,?,?)",
            (run_id, step_id, kind, detail, now()),
        )

    def workflow_status(
        self, run_id: str, new: WorkflowStatus, error: str | None = None
    ) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            check_transition(WorkflowStatus(row["status"]), new)
            db.execute(
                "UPDATE workflow_runs SET status=?, "
                "started_at=CASE WHEN ?='RUNNING' AND started_at IS NULL "
                "THEN ? ELSE started_at END, "
                "finished_at=CASE WHEN ? IN ('COMPLETED','FAILED','CANCELLED') "
                "THEN ? ELSE finished_at END, "
                "error=COALESCE(?,error) WHERE run_id=?",
                (new, new, now(), new, now(), error, run_id),
            )
            self._event(db, run_id, None, new, error)

    def step_status(
        self,
        run_id: str,
        step_id: str,
        new: StepStatus,
        *,
        result: dict[str, str | bool | None] | None = None,
        execution_id: str | None = None,
    ) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,attempt FROM workflow_steps "
                "WHERE run_id=? AND step_id=?",
                (run_id, step_id),
            ).fetchone()
            if row is None:
                raise KeyError(step_id)
            check_transition(StepStatus(row["status"]), new)
            attempt = row["attempt"] + (new == StepStatus.RUNNING)
            db.execute(
                "UPDATE workflow_steps SET status=?, attempt=?, "
                "execution_id=COALESCE(?,execution_id), "
                "started_at=CASE WHEN ?='RUNNING' THEN ? ELSE started_at END, "
                "finished_at=CASE WHEN ? IN "
                "('SUCCEEDED','FAILED','SKIPPED','CANCELLED','INTERRUPTED') "
                "THEN ? ELSE finished_at END, "
                "result=COALESCE(?,result) WHERE run_id=? AND step_id=?",
                (
                    new,
                    attempt,
                    execution_id,
                    new,
                    now(),
                    new,
                    now(),
                    json.dumps(result) if result is not None else None,
                    run_id,
                    step_id,
                ),
            )
            if new == StepStatus.RUNNING:
                db.execute(
                    "INSERT INTO workflow_attempts VALUES (?,?,?,?,?,?,?)",
                    (run_id, step_id, attempt, execution_id, new, None, now()),
                )
            elif row["status"] == StepStatus.RUNNING:
                db.execute(
                    "UPDATE workflow_attempts SET status=?,result=? "
                    "WHERE run_id=? AND step_id=? AND attempt=?",
                    (
                        new,
                        json.dumps(result) if result is not None else None,
                        run_id,
                        step_id,
                        row["attempt"],
                    ),
                )
            if (
                row["status"] == StepStatus.WAITING_APPROVAL
                and new == StepStatus.CANCELLED
            ):
                db.execute(
                    "UPDATE workflow_approvals SET status='CANCELLED' "
                    "WHERE run_id=? AND step_id=? AND status='PENDING'",
                    (run_id, step_id),
                )
            if new == StepStatus.WAITING_APPROVAL:
                db.execute(
                    "INSERT INTO workflow_approvals(run_id,step_id,status) "
                    "VALUES (?,?,'PENDING')",
                    (run_id, step_id),
                )
            self._event(
                db,
                run_id,
                step_id,
                new,
                json.dumps(result) if result is not None else None,
            )

    def decide(self, run_id: str, step_id: str, actor: str, approved: bool) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM workflow_approvals WHERE run_id=? AND step_id=?",
                (run_id, step_id),
            ).fetchone()
            if row is None or row["status"] != "PENDING":
                raise ValueError("approval is absent or already decided")
            db.execute(
                "UPDATE workflow_approvals SET status=?,decided_by=?,decided_at=? "
                "WHERE run_id=? AND step_id=?",
                ("APPROVED" if approved else "REJECTED", actor, now(), run_id, step_id),
            )
            new = StepStatus.SUCCEEDED if approved else StepStatus.FAILED
            db.execute(
                "UPDATE workflow_steps SET status=?,finished_at=?,result=? "
                "WHERE run_id=? AND step_id=?",
                (new, now(), json.dumps({"approved": approved}), run_id, step_id),
            )
            self._event(db, run_id, step_id, new, actor)

    def approval_status(self, run_id: str, step_id: str) -> str | None:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT status FROM workflow_approvals WHERE run_id=? AND step_id=?",
                (run_id, step_id),
            ).fetchone()
        return str(row["status"]) if row else None

    def events(self, run_id: str) -> list[dict[str, str | int | None]]:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_events WHERE run_id=? ORDER BY event_id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]
