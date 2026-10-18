"""ADR proposals and authorized decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from aec.communication.broker import Broker
from aec.persistence.database import Database
from aec.security.identities import Identity, Role
from aec.workflows.repository import WorkflowRepository

from .renderer import render, safe


class ADR(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+$")]
    title: Annotated[str, Field(min_length=1)]
    context: Annotated[str, Field(min_length=1)]
    problem: Annotated[str, Field(min_length=1)]
    alternatives: list[str] = Field(default_factory=list)
    decision: Annotated[str, Field(min_length=1)]
    rationale: Annotated[str, Field(min_length=1)]
    consequences: Annotated[str, Field(min_length=1)]
    references: list[str] = Field(default_factory=list)
    status: Literal["PROPOSED", "ACCEPTED", "REJECTED", "SUPERSEDED"] = "PROPOSED"
    date: date
    authorization_run_id: str | None = None


@dataclass(frozen=True)
class ADRRecord:
    adr_id: str
    revision: int
    run_id: str
    task_id: str
    status: str
    content: str
    sha256: str


def render_adr(adr: ADR, database: Database) -> str:
    if adr.status != "PROPOSED":
        if not adr.authorization_run_id:
            raise PermissionError(
                "decision status requires a human authorization record"
            )
        try:
            definition = WorkflowRepository(database).definition(
                adr.authorization_run_id
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise PermissionError("invalid ADR authorization workflow") from exc
        with database.connect() as db:
            approval = db.execute(
                "SELECT step_id,status,decided_by FROM workflow_approvals "
                "WHERE run_id=?",
                (adr.authorization_run_id,),
            ).fetchone()
        expected = "REJECTED" if adr.status == "REJECTED" else "APPROVED"
        if (
            approval is None
            or not approval["decided_by"]
            or approval["status"] != expected
            or definition.metadata.get("adr_id") != adr.id
            or not any(
                step.id == approval["step_id"] and step.type == "human_approval"
                for step in definition.steps
            )
        ):
            raise PermissionError("matching ADR authorization record not found")
    return render(
        "adr",
        {
            "id": safe(adr.id),
            "title": safe(adr.title),
            "context": safe(adr.context),
            "problem": safe(adr.problem),
            "alternatives": "\n".join(f"- {safe(v)}" for v in adr.alternatives)
            or "- indisponível",
            "decision": safe(adr.decision),
            "rationale": safe(adr.rationale),
            "consequences": safe(adr.consequences),
            "references": "\n".join(f"- {safe(v)}" for v in adr.references)
            or "- indisponível",
            "status": adr.status,
            "date": adr.date.isoformat(),
        },
    )


def get_adr(database: Database, adr_id: str, revision: int) -> ADRRecord:
    with database.connect() as db:
        row = db.execute(
            "SELECT * FROM adr_records WHERE adr_id=? AND revision=?",
            (adr_id, revision),
        ).fetchone()
    if row is None:
        raise KeyError((adr_id, revision))
    record = ADRRecord(
        adr_id=row["adr_id"],
        revision=row["revision"],
        run_id=row["run_id"],
        task_id=row["task_id"],
        status=row["status"],
        content=row["content"],
        sha256=row["sha256"],
    )
    if hashlib.sha256(record.content.encode()).hexdigest() != record.sha256:
        raise ValueError("ADR content hash mismatch")
    return record


def record_adr(
    adr: ADR, broker: Broker, actor: Identity, run_id: str, task_id: str
) -> ADRRecord:
    broker._identity(actor)
    if actor.role not in {Role.ORCHESTRATOR, Role.DOCUMENTATION}:
        raise PermissionError("documentation role required to record ADR")
    database = broker.db
    content = render_adr(adr, database)
    if adr.status != "PROPOSED" and WorkflowRepository(database).definition(
        adr.authorization_run_id or ""
    ).metadata.get("engineering_run_id") != run_id:
        raise PermissionError("ADR authorization does not belong to engineering run")
    digest = hashlib.sha256(content.encode()).hexdigest()
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute(
            "SELECT task_id FROM engineering_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None or run["task_id"] != task_id:
            raise ValueError("ADR task does not match engineering run")
        previous = db.execute(
            "SELECT revision FROM adr_records WHERE adr_id=? AND run_id=? AND sha256=?",
            (adr.id, run_id, digest),
        ).fetchone()
        if previous is None:
            revision = db.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM adr_records WHERE adr_id=?",
                (adr.id,),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO adr_records VALUES (?,?,?,?,?,?,?,?)",
                (
                    adr.id, revision, run_id, task_id, adr.status, content, digest,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        else:
            revision = previous["revision"]
    return get_adr(database, adr.id, revision)
