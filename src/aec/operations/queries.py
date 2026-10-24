"""Allowlisted views of the existing AEC SQLite event and state tables."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from aec.execution.models import ExecutionStatus
from aec.workflows.models import StepStatus, WorkflowStatus


class OperationalReader:
    def __init__(self, database: Path) -> None:
        if not database.is_file():
            raise FileNotFoundError(database)
        self.database = database.resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"{self.database.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _rows(
        db: sqlite3.Connection, sql: str, parameters: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]:
        return [dict(row) for row in db.execute(sql, parameters)]

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            runs = self._rows(
                db,
                "SELECT status,COUNT(*) AS count FROM workflow_runs "
                "GROUP BY status ORDER BY status",
            )
            executions = self._rows(
                db,
                "SELECT status,COUNT(*) AS count FROM executions "
                "GROUP BY status ORDER BY status",
            )
            pending = db.execute(
                "SELECT COUNT(*) FROM workflow_approvals WHERE status='PENDING'"
            ).fetchone()[0]
            pending += db.execute(
                "SELECT COUNT(*) FROM methodology_proposals "
                "WHERE status='AWAITING_APPROVAL'"
            ).fetchone()[0]
        for row in runs:
            row["status_known"] = row["status"] in WorkflowStatus._value2member_map_
        for row in executions:
            row["status_known"] = (
                row["status"] in ExecutionStatus._value2member_map_
            )
        return {"runs": runs, "executions": executions, "pending_approvals": pending}

    def runs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = self._rows(
                db,
                "SELECT run_id,status,created_at,started_at,finished_at "
                "FROM workflow_runs ORDER BY created_at,run_id",
            )
        for row in rows:
            row["status_known"] = row["status"] in WorkflowStatus._value2member_map_
        return rows

    def inspect(self, run_id: str) -> dict[str, Any]:
        with self._connect() as db:
            run = db.execute(
                "SELECT run_id,status,definition_hash,created_at,"
                "started_at,finished_at "
                "FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            steps = self._rows(
                db,
                "SELECT step_id,status,attempt,execution_id,started_at,finished_at "
                "FROM workflow_steps WHERE run_id=? ORDER BY step_id",
                (run_id,),
            )
            skills = self._rows(
                db,
                "SELECT step_id,skill_id,sha256,origin,approved_by "
                "FROM workflow_skill_bindings WHERE run_id=? "
                "ORDER BY step_id,skill_id",
                (run_id,),
            )
            budget = db.execute(
                "SELECT content,sha256 FROM run_execution_budgets WHERE run_id=?",
                (run_id,),
            ).fetchone()
            reviews = self._rows(
                db,
                "SELECT message_id,type,content FROM messages WHERE run_id=? "
                "AND type IN ('FINDING_REPORTED','VALIDATION_RESULT') "
                "ORDER BY created_at,message_id",
                (run_id,),
            )
            binding = db.execute(
                "SELECT methodology_id,version,sha256 FROM methodology_run_bindings "
                "WHERE run_id=?",
                (run_id,),
            ).fetchone()
            executions = self._rows(
                db,
                "SELECT execution_id,task_id,agent_id,executor,status,"
                "created_at,updated_at "
                "FROM executions WHERE run_id=? ORDER BY created_at,execution_id",
                (run_id,),
            )
        for step in steps:
            step["status_known"] = step["status"] in StepStatus._value2member_map_
        for execution in executions:
            execution["status_known"] = (
                execution["status"] in ExecutionStatus._value2member_map_
            )
        safe_reviews = []
        for review in reviews:
            try:
                envelope = json.loads(review["content"])
                payload = envelope.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {}
            except (TypeError, ValueError, AttributeError):
                payload = {}
            safe_reviews.append({
                "message_id": review["message_id"],
                "type": review["type"],
                "artifact_id": payload.get("artifact_id"),
                "severity": payload.get("severity") if review["type"]
                == "FINDING_REPORTED" else None,
                "passed": payload.get("passed") if review["type"]
                == "VALIDATION_RESULT" else None,
            })
        resource_policy: dict[str, Any] | None = None
        if budget is not None:
            try:
                content = budget["content"]
                parsed = json.loads(content)
                if (hashlib.sha256(content.encode()).hexdigest() != budget["sha256"]
                        or not isinstance(parsed, dict)):
                    raise ValueError("invalid budget")
                resource_policy = {
                    "version": parsed.get("version"),
                    "max_invocations_per_run": parsed.get("max_invocations_per_run"),
                    "max_concurrent_agents": parsed.get("max_concurrent_agents"),
                    "max_retries": parsed.get("max_retries"),
                    "execution_timeout_seconds": parsed.get(
                        "execution_timeout_seconds"
                    ),
                    "max_duration_seconds": parsed.get("max_duration_seconds"),
                    "max_iterations": parsed.get("max_iterations"),
                    "sha256": budget["sha256"],
                }
            except (TypeError, ValueError):
                resource_policy = {"state": "UNKNOWN"}
        return {
            "run": {
                **dict(run),
                "status_known": run["status"] in WorkflowStatus._value2member_map_,
            },
            "methodology": dict(binding) if binding else None,
            "steps": steps,
            "executions": executions,
            "skills": skills,
            "reviews": safe_reviews,
            "resource_policy": resource_policy,
        }

    def timeline(self, run_id: str) -> list[dict[str, Any]]:
        inspected = self.inspect(run_id)
        events: list[dict[str, Any]] = []
        if inspected["resource_policy"] is not None:
            events.append({
                "at": inspected["run"]["created_at"],
                "source": "run_execution_budgets",
                "source_id": run_id,
                "kind": "POLICY_SNAPSHOT",
                "run_id": run_id,
                **inspected["resource_policy"],
            })
        binding = inspected["methodology"]
        if binding is not None:
            events.append(
                {
                    "at": inspected["run"]["created_at"],
                    "source": "methodology_run_bindings",
                    "source_id": run_id,
                    "kind": "BINDING_SNAPSHOT",
                    "run_id": run_id,
                    **binding,
                }
            )
        for skill in inspected["skills"]:
            events.append({
                "at": inspected["run"]["created_at"],
                "source": "workflow_skill_bindings",
                "source_id": skill["skill_id"],
                "kind": "SKILL_BINDING_SNAPSHOT",
                "run_id": run_id,
                **skill,
            })
        with self._connect() as db:
            for row in self._rows(
                db,
                "SELECT event_id,step_id,kind,created_at FROM workflow_events "
                "WHERE run_id=? ORDER BY event_id",
                (run_id,),
            ):
                events.append(
                    {
                        "at": row["created_at"],
                        "source": "workflow_events",
                        "source_id": row["event_id"],
                        "kind": row["kind"],
                        "step_id": row["step_id"],
                        "run_id": run_id,
                    }
                )
            for row in self._rows(
                db,
                "SELECT message_id,task_id,sender,recipient,type,content,created_at "
                "FROM messages WHERE run_id=? ORDER BY created_at,message_id",
                (run_id,),
            ):
                try:
                    envelope = json.loads(row.pop("content"))
                    if not isinstance(envelope, dict):
                        envelope = {}
                except (TypeError, ValueError):
                    envelope = {}
                refs = envelope.get("artifact_refs")
                payload = envelope.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                safe_refs = (
                    [
                        {
                            "artifact_id": ref.get("artifact_id"),
                            "sha256": ref.get("sha256"),
                        }
                        for ref in refs
                        if isinstance(ref, dict)
                    ]
                    if isinstance(refs, list)
                    else []
                )
                events.append(
                    {
                        "at": row["created_at"],
                        "source": "messages",
                        "source_id": row["message_id"],
                        "kind": row["type"],
                        "run_id": run_id,
                        "task_id": row["task_id"],
                        "agent_id": row["sender"],
                        "recipient": row["recipient"],
                        "message_id": row["message_id"],
                        "correlation_id": envelope.get("correlation_id"),
                        "causation_id": envelope.get("causation_id"),
                        "artifact_refs": safe_refs,
                        "finding_severity": payload.get("severity")
                        if row["type"] == "FINDING_REPORTED" else None,
                        "gate_passed": payload.get("passed")
                        if row["type"] == "VALIDATION_RESULT" else None,
                    }
                )
            for row in self._rows(
                db,
                "SELECT e.event_id,e.message_id,e.agent_id,e.kind,e.detail,"
                "e.created_at "
                "FROM events e JOIN messages m ON m.message_id=e.message_id "
                "WHERE m.run_id=? ORDER BY e.event_id",
                (run_id,),
            ):
                events.append(
                    {
                        "at": row["created_at"],
                        "source": "events",
                        "source_id": row["event_id"],
                        "kind": row["kind"],
                        "run_id": run_id,
                        "message_id": row["message_id"],
                        "agent_id": row["agent_id"],
                        "block_reason": row["detail"]
                        if row["kind"] == "EXECUTION_BLOCKED"
                        and row["detail"] in {
                            "max_invocations_per_run", "execution_timeout_seconds",
                            "max_duration_seconds",
                        } else None,
                    }
                )
            for row in self._rows(
                db,
                "SELECT e.execution_id,e.task_id,e.agent_id,e.status,"
                "e.created_at,e.updated_at,s.requested_model,"
                "s.reasoning_effort,s.effective_model "
                "FROM executions e LEFT JOIN execution_settings s "
                "ON s.execution_id=e.execution_id "
                "WHERE e.run_id=? ORDER BY e.created_at,e.execution_id",
                (run_id,),
            ):
                events.append(
                    {
                        "at": row["updated_at"],
                        "source": "executions",
                        "source_id": row["execution_id"],
                        "kind": "CURRENT_STATUS_RECORDED",
                        "run_id": run_id,
                        "task_id": row["task_id"],
                        "agent_id": row["agent_id"],
                        "execution_id": row["execution_id"],
                        "persisted_status": row["status"],
                        "status_known": row["status"]
                        in ExecutionStatus._value2member_map_,
                        "requested_model": row["requested_model"],
                        "reasoning_effort": row["reasoning_effort"],
                        "effective_model": row["effective_model"],
                    }
                )
            for row in self._rows(
                db,
                "SELECT step_id,status,decided_by,decided_at FROM workflow_approvals "
                "WHERE run_id=? AND decided_at IS NOT NULL ORDER BY decided_at,step_id",
                (run_id,),
            ):
                events.append(
                    {
                        "at": row["decided_at"],
                        "source": "workflow_approvals",
                        "source_id": row["step_id"],
                        "kind": row["status"],
                        "run_id": run_id,
                        "step_id": row["step_id"],
                        "decided_by": row["decided_by"],
                    }
                )
        return sorted(
            events,
            key=lambda event: (
                event["at"] or "",
                event["source"],
                str(event["source_id"]),
            ),
        )

    def agents(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return self._rows(
                db, "SELECT a.agent_id,a.role,a.created_at,"
                "(SELECT COUNT(*) FROM executions e WHERE e.agent_id=a.agent_id) "
                "AS invocations,"
                "(SELECT MAX(m.created_at) FROM messages m "
                "WHERE m.sender=a.agent_id) AS last_message_at "
                "FROM agents a ORDER BY a.agent_id"
            )

    def pending_approvals(self) -> dict[str, list[dict[str, Any]]]:
        with self._connect() as db:
            return {
                "workflows": self._rows(
                    db,
                    "SELECT run_id,step_id,status FROM workflow_approvals "
                    "WHERE status='PENDING' ORDER BY run_id,step_id",
                ),
                "methodologies": self._rows(
                    db,
                    "SELECT proposal_id,status FROM methodology_proposals "
                    "WHERE status='AWAITING_APPROVAL' ORDER BY proposal_id",
                ),
            }

    def processes(self, run_id: str) -> list[dict[str, Any]]:
        self.inspect(run_id)
        with self._connect() as db:
            rows = self._rows(
                db,
                "SELECT e.run_id,e.execution_id,e.executor,e.agent_id,e.status,"
                "e.working_directory AS workspace,p.pid,p.pgid,p.boot_id,"
                "p.start_ticks,p.recorded_at,s.unit_name AS scope,"
                "r.reconciled_at FROM executions e "
                "LEFT JOIN execution_processes p ON p.execution_id=e.execution_id "
                "LEFT JOIN execution_scopes s ON s.execution_id=e.execution_id "
                "LEFT JOIN execution_reconciliations r "
                "ON r.execution_id=e.execution_id "
                "WHERE e.run_id=? ORDER BY e.created_at,e.execution_id",
                (run_id,),
            )
        for row in rows:
            row["quarantined"] = (
                row["status"] == "INTERRUPTED" and row["reconciled_at"] is None
            )
            row["reconciliation_pending"] = row["quarantined"]
            row["observed_state"] = self._observe(row)
            row["status_known"] = row["status"] in ExecutionStatus._value2member_map_
        return rows

    @staticmethod
    def _observe(row: dict[str, Any]) -> str:
        pid = row["pid"]
        if not isinstance(pid, int):
            return "UNKNOWN"
        try:
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
            if row["boot_id"] != boot or row["start_ticks"] != int(stat[19]):
                return "UNKNOWN"
            if os.getpgid(pid) != row["pgid"]:
                return "UNKNOWN"
        except (OSError, IndexError, ValueError):
            return "UNKNOWN"
        return "PRESENT"

    def metrics(self) -> dict[str, Any]:
        with self._connect() as db:
            by_agent = self._rows(
                db,
                "SELECT agent_id,COUNT(*) AS invocations FROM executions "
                "GROUP BY agent_id ORDER BY agent_id",
            )
            by_status = self._rows(
                db,
                "SELECT status,COUNT(*) AS count FROM executions "
                "GROUP BY status ORDER BY status",
            )
            by_run = self._rows(
                db, "SELECT run_id,COUNT(*) AS invocations FROM executions "
                "GROUP BY run_id ORDER BY run_id"
            )
            retries = db.execute(
                "SELECT COUNT(*) FROM workflow_attempts WHERE attempt>1"
            ).fetchone()[0]
            runs = db.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0]
            invocations = db.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
            settings = self._rows(
                db,
                "SELECT requested_model,reasoning_effort,COUNT(*) AS invocations "
                "FROM execution_settings GROUP BY requested_model,reasoning_effort "
                "ORDER BY requested_model,reasoning_effort",
            )
            results = self._rows(
                db, "SELECT result FROM executions WHERE result IS NOT NULL"
            )
        token_totals: dict[str, int | float] = {}
        duration_total = 0.0
        durations_reported = 0
        for row in results:
            try:
                result = json.loads(row["result"])
                usage = result.get("usage", {}) if isinstance(result, dict) else {}
                if (isinstance(result, dict) and result.get("started_at")
                        and result.get("finished_at")):
                    duration_total += (
                        datetime.fromisoformat(result["finished_at"])
                        - datetime.fromisoformat(result["started_at"])
                    ).total_seconds()
                    durations_reported += 1
                if isinstance(usage, dict):
                    for key, value in usage.items():
                        if (key.endswith("_tokens") and isinstance(value, (int, float))
                                and not isinstance(value, bool)):
                            token_totals[key] = token_totals.get(key, 0) + value
            except (TypeError, ValueError):
                continue
        return {
            "workflow_runs": runs,
            "invocations": invocations,
            "by_agent": by_agent,
            "by_run": by_run,
            "by_status": by_status,
            "retries": retries,
            "duration_seconds_reported": duration_total
            if durations_reported else "NOT_AVAILABLE",
            "requested_settings": settings,
            "reported_tokens": token_totals or "NOT_AVAILABLE",
            "effective_model": "UNKNOWN",
        }

    def methodologies(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return self._rows(
                db,
                "SELECT v.methodology_id,v.version,v.sha256,v.published_at,"
                "v.approved_by,CASE WHEN a.version=v.version "
                "THEN 1 ELSE 0 END AS active "
                "FROM methodology_versions v LEFT JOIN methodology_active a "
                "ON a.methodology_id=v.methodology_id "
                "ORDER BY v.methodology_id,v.version",
            )
