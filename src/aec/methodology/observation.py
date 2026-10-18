"""Read typed engineering records without interpreting message prose."""

from __future__ import annotations

from datetime import datetime

from pydantic import TypeAdapter

from aec.engineering.gates import GateResult
from aec.engineering.models import QAReviewResult
from aec.persistence.database import Database

from .contracts import MethodologyObservation

GATE_RESULTS = TypeAdapter(list[GateResult])


class ObservationCollector:
    def __init__(self, db: Database) -> None:
        self.db = db

    def collect(self) -> list[MethodologyObservation]:
        observations: list[MethodologyObservation] = []
        with self.db.connect() as connection:
            runs = connection.execute(
                "SELECT * FROM engineering_runs ORDER BY created_at, run_id"
            ).fetchall()
            for run in runs:
                iterations = connection.execute(
                    "SELECT * FROM engineering_iterations WHERE run_id=? "
                    "ORDER BY number",
                    (run["run_id"],),
                ).fetchall()
                binding = connection.execute(
                    "SELECT methodology_id,version FROM methodology_run_bindings "
                    "WHERE run_id=?",
                    (run["run_id"],),
                ).fetchone()
                reviews = [
                    QAReviewResult.model_validate_json(item["review"])
                    for item in iterations
                    if item["review"]
                ]
                gates = [
                    GATE_RESULTS.validate_json(item["gates"])
                    for item in iterations
                    if item["gates"]
                ]
                approval = connection.execute(
                    "SELECT decided_at FROM workflow_approvals WHERE run_id=? "
                    "AND status='APPROVED'",
                    (f"{run['run_id']}-approval",),
                ).fetchone()
                prefix = f"{run['run_id']}-"
                messages = connection.execute(
                    "SELECT message_id FROM messages WHERE run_id=? "
                    "OR substr(run_id,1,?)=? ORDER BY created_at,message_id",
                    (run["run_id"], len(prefix), prefix),
                ).fetchall()
                interventions = connection.execute(
                    "SELECT COUNT(*) FROM workflow_approvals "
                    "WHERE substr(run_id,1,?)=? AND decided_at IS NOT NULL",
                    (len(prefix), prefix),
                ).fetchone()[0]
                invocations = connection.execute(
                    "SELECT COUNT(*) FROM executions WHERE substr(run_id,1,?)=?",
                    (len(prefix), prefix),
                ).fetchone()[0]
                observations.append(
                    MethodologyObservation(
                        run_id=run["run_id"],
                        task_id=run["task_id"],
                        methodology_id=binding["methodology_id"]
                        if binding
                        else "dev-adversarial",
                        version=binding["version"] if binding else "1.0.0",
                        started_at=datetime.fromisoformat(run["created_at"]),
                        finished_at=None,
                        status=run["status"],
                        review_iterations=len(reviews) if reviews else None,
                        first_review_passed=reviews[0].review_status == "PASSED"
                        if reviews
                        else None,
                        required_rework=any(
                            review.review_status == "FAILED" for review in reviews
                        )
                        if reviews
                        else None,
                        findings=tuple(
                            f.category
                            for review in reviews
                            for f in review.findings
                            if f.status == "REPRODUCED"
                        ),
                        message_ids=tuple(row["message_id"] for row in messages),
                        artifact_refs=tuple(
                            ref
                            for review in reviews
                            for ref in (
                                review.evidence_refs
                                + [
                                    evidence
                                    for finding in review.findings
                                    for evidence in finding.evidence_refs
                                ]
                            )
                        ),
                        gates_passed=all(
                            gate.status == "PASSED" for group in gates for gate in group
                        )
                        if gates
                        else None,
                        human_interventions=interventions,
                        agent_invocations=invocations,
                        approval_at=datetime.fromisoformat(approval["decided_at"])
                        if approval
                        else None,
                    )
                )
        return observations
