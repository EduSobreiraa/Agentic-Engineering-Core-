"""Execution and session state in the ACP SQLite database."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal

from aec.persistence.database import Database

from .locking import LocalLock
from .models import AgentSession, ExecutionRequest, ExecutionResult, ExecutionStatus


class ExecutionRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    def reconcile(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT execution_id FROM executions WHERE status IN (?, ?)",
                (ExecutionStatus.STARTING, ExecutionStatus.RUNNING),
            ).fetchall()
            interrupted = 0
            for row in active:
                lock = LocalLock(self.db.path, f"execution:{row['execution_id']}")
                try:
                    if lock.try_acquire():
                        db.execute(
                            "UPDATE executions SET status=?,updated_at=? "
                            "WHERE execution_id=?",
                            (ExecutionStatus.INTERRUPTED, now, row["execution_id"]),
                        )
                        interrupted += 1
                finally:
                    lock.close()
            return interrupted

    def require_workspace_available(self, workspace: Path) -> None:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT e.execution_id,e.working_directory FROM executions e "
                "LEFT JOIN execution_reconciliations r "
                "ON r.execution_id=e.execution_id "
                "WHERE e.status IN ('STARTING','RUNNING','INTERRUPTED') "
                "AND r.execution_id IS NULL"
            ).fetchall()
        for row in rows:
            other = Path(row["working_directory"])
            if (
                workspace == other
                or workspace in other.parents
                or other in workspace.parents
            ):
                raise RuntimeError(
                    f"workspace awaits reconciliation of {row['execution_id']}"
                )

    def record_process(self, execution_id: str, pid: int) -> None:
        if pid <= 0:
            raise ValueError("process ID must be positive")
        pgid = os.getpgid(pid)
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            stat = Path(f"/proc/{pid}/stat").read_text()
            start_ticks = int(stat.rsplit(") ", 1)[1].split()[19])
        except (OSError, IndexError, ValueError):
            boot_id, start_ticks = None, None
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM executions WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if row is None or row["status"] != ExecutionStatus.RUNNING:
                raise ValueError("process does not belong to a running execution")
            db.execute(
                "INSERT INTO execution_processes VALUES (?,?,?,?,?,?)",
                (execution_id, pid, pgid, boot_id, start_ticks,
                 datetime.now(timezone.utc).isoformat()),
            )

    def process_info(self, execution_id: str) -> dict[str, int | str | None] | None:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT pid,pgid,boot_id,start_ticks,recorded_at "
                "FROM execution_processes WHERE execution_id=?", (execution_id,)
            ).fetchone()
        return dict(row) if row else None

    def record_scope(self, execution_id: str, unit_name: str) -> None:
        if re.fullmatch(r"aec-[0-9a-f]{64}\.scope", unit_name) is None:
            raise ValueError("invalid execution scope name")
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not boot_id:
            raise RuntimeError("boot identity is unavailable")
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM executions WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if row is None or row["status"] != ExecutionStatus.RUNNING:
                raise ValueError("scope does not belong to a running execution")
            db.execute(
                "INSERT INTO execution_scopes VALUES (?,?,?,?)",
                (execution_id, unit_name, boot_id,
                 datetime.now(timezone.utc).isoformat()),
            )

    def scope_info(self, execution_id: str) -> dict[str, str] | None:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT unit_name,boot_id,recorded_at FROM execution_scopes "
                "WHERE execution_id=?", (execution_id,)
            ).fetchone()
        return dict(row) if row else None

    def scope_present(self, execution_id: str) -> bool:
        scope = self.scope_info(execution_id)
        if scope is None:
            return False
        try:
            current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if current_boot != scope["boot_id"]:
                return False
            result = subprocess.run(
                ["systemctl", "--user", "show", scope["unit_name"],
                 "-p", "LoadState", "-p", "ActiveState", "--no-pager"],
                capture_output=True, text=True, check=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("execution scope cannot be inspected") from exc
        states = dict(
            line.split("=", 1)
            for line in result.stdout.splitlines()
            if "=" in line
        )
        if states.get("ActiveState") == "inactive" and states.get("LoadState") in {
            "not-found", "loaded"
        }:
            return False
        if "LoadState" not in states or "ActiveState" not in states:
            raise RuntimeError("execution scope state is unavailable")
        return True

    def processes_present(self, execution_id: str) -> bool:
        return self.scope_present(execution_id) or self.process_group_present(
            execution_id
        )

    def process_group_present(self, execution_id: str) -> bool:
        process = self.process_info(execution_id)
        if process is None:
            return False
        boot_id = process["boot_id"]
        if isinstance(boot_id, str):
            try:
                current_boot = (
                    Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                )
                if current_boot != boot_id:
                    return False
            except OSError:
                pass
        pgid = process["pgid"]
        if not isinstance(pgid, int):
            return True
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        return True

    def record_reconciliation(
        self, execution_id: str, actor: str, evidence: str,
        *, effects_status: Literal["VERIFIED", "REVERTED", "UNKNOWN"],
        confirm_workspace_inspected: bool,
    ) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            execution = db.execute(
                "SELECT status FROM executions WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if execution is None or execution["status"] != ExecutionStatus.INTERRUPTED:
                raise ValueError("execution is not interrupted")
            if not confirm_workspace_inspected:
                raise RuntimeError(
                    "operator must attest workspace and process inspection"
                )
            if self.processes_present(execution_id):
                raise RuntimeError("execution processes are still present")
            db.execute(
                "INSERT INTO execution_reconciliations VALUES (?,?,?,?,?)",
                (
                    execution_id, actor, evidence,
                    datetime.now(timezone.utc).isoformat(), effects_status,
                ),
            )

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
