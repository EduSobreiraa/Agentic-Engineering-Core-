"""ADR proposals and authorized decisions."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from aec.persistence.database import Database

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


def render_adr(adr: ADR, database: Database) -> str:
    if adr.status != "PROPOSED":
        if not adr.authorization_run_id:
            raise PermissionError(
                "decision status requires a human authorization record"
            )
        with database.connect() as db:
            approval = db.execute(
                "SELECT a.status,a.decided_by,r.definition FROM workflow_approvals a "
                "JOIN workflow_runs r ON r.run_id=a.run_id WHERE a.run_id=?",
                (adr.authorization_run_id,),
            ).fetchone()
        expected = "REJECTED" if adr.status == "REJECTED" else "APPROVED"
        if (
            approval is None
            or not approval["decided_by"]
            or approval["status"] != expected
            or json.loads(approval["definition"]).get("metadata", {}).get("adr_id")
            != adr.id
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
