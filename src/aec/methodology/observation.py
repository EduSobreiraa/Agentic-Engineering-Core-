"""Read typed engineering records without interpreting message prose."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import cast

from pydantic import TypeAdapter

from aec.communication.broker import Broker
from aec.communication.schemas import ArtifactRef
from aec.documentation.collector import DocumentationCollector
from aec.engineering.gates import GateResult
from aec.engineering.models import QAReviewResult
from aec.methodologies.arena import arena_comparison
from aec.persistence.database import Database
from aec.workflows.loader import definition_hash
from aec.workflows.models import WorkflowDefinition

from .contracts import MethodologyObservation
from .governance import MethodologyRegistry

GATE_RESULTS = TypeAdapter(list[GateResult])


class ObservationCollector:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.registry = MethodologyRegistry(db)
        self.documents = DocumentationCollector(Broker(db.path))

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
                methodology_id = (
                    binding["methodology_id"] if binding else "dev-adversarial"
                )
                version = binding["version"] if binding else "1.0.0"
                try:
                    required = self.registry.get(
                        methodology_id, version
                    ).required_documents
                except KeyError:
                    required = frozenset()
                valid_documents: set[str] = set()
                if required:
                    records = connection.execute(
                        "SELECT document_type,content,sha256,artifact_ref,"
                        "validation_status FROM documentation_records WHERE run_id=?",
                        (run["run_id"],),
                    ).fetchall()
                    for record in records:
                        if (
                            record["document_type"] not in required
                            or record["validation_status"]
                            not in {"VALID", "VALID_WITH_WARNINGS"}
                            or hashlib.sha256(record["content"].encode()).hexdigest()
                            != record["sha256"]
                        ):
                            continue
                        try:
                            ref = ArtifactRef.model_validate_json(
                                record["artifact_ref"]
                            )
                        except ValueError:
                            continue
                        if (
                            ref.uri == f"artifact://{run['run_id']}/{ref.artifact_id}"
                            and self.documents.artifact_intact(ref)
                        ):
                            valid_documents.add(record["document_type"])
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
                        methodology_id=methodology_id,
                        version=version,
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
                        required_documents=len(required) if required else None,
                        valid_documents=len(valid_documents) if required else None,
                    )
                )
            workflows = connection.execute(
                "SELECT w.*,b.methodology_id,b.version,b.sha256 AS methodology_sha "
                "FROM workflow_runs w "
                "JOIN methodology_run_bindings b ON b.run_id=w.run_id "
                "LEFT JOIN engineering_runs e ON e.run_id=w.run_id "
                "WHERE e.run_id IS NULL ORDER BY w.created_at,w.run_id"
            ).fetchall()
            for run in workflows:
                published = connection.execute(
                    "SELECT sha256 FROM methodology_versions "
                    "WHERE methodology_id=? AND version=?",
                    (run["methodology_id"], run["version"]),
                ).fetchone()
                version = self.registry.get(run["methodology_id"], run["version"])
                if (
                    published is None
                    or published["sha256"] != run["methodology_sha"]
                    or definition_hash(version.definition) != run["definition_hash"]
                    or definition_hash(
                        WorkflowDefinition.model_validate_json(run["definition"])
                    )
                    != run["definition_hash"]
                ):
                    raise ValueError("workflow methodology binding mismatch")
                steps = connection.execute(
                    "SELECT s.step_id,s.status,s.result,d.definition "
                    "FROM workflow_steps s "
                    "JOIN workflow_runs d ON d.run_id=s.run_id "
                    "WHERE s.run_id=? ORDER BY s.step_id",
                    (run["run_id"],),
                ).fetchall()
                definition = json.loads(steps[0]["definition"]) if steps else {}
                kinds = {
                    step["id"]: step["type"] for step in definition.get("steps", [])
                }
                validators = {
                    step["id"]: step["validator"]
                    for step in definition.get("steps", [])
                    if step["type"] == "validation"
                }
                required_gates = version.mandatory_gates
                validations = {
                    validators[step["step_id"]]: json.loads(step["result"]).get(
                        "passed"
                    )
                    for step in steps
                    if step["result"] and kinds.get(step["step_id"]) == "validation"
                }
                approval = connection.execute(
                    "SELECT decided_at FROM workflow_approvals "
                    "WHERE run_id=? AND status='APPROVED'",
                    (run["run_id"],),
                ).fetchone()
                interventions = connection.execute(
                    "SELECT COUNT(*) FROM workflow_approvals "
                    "WHERE run_id=? AND decided_at IS NOT NULL",
                    (run["run_id"],),
                ).fetchone()[0]
                invocations = connection.execute(
                    "SELECT COUNT(*) FROM executions WHERE run_id=?",
                    (run["run_id"],),
                ).fetchone()[0]
                if run["methodology_id"] == "implementation-arena":
                    comparison = arena_comparison(self.db, run["run_id"])
                    base_run = run["run_id"].removesuffix("-approval")
                    child_runs = [
                        f"{base_run}-{item['agent']}"
                        for item in cast(
                            list[dict[str, object]], comparison["candidates"]
                        )
                    ]
                    if comparison["qa_run_id"]:
                        child_runs.append(str(comparison["qa_run_id"]))
                    invocations = connection.execute(
                        "SELECT COUNT(*) FROM executions WHERE run_id IN ("
                        + ",".join("?" for _ in child_runs)
                        + ")",
                        child_runs,
                    ).fetchone()[0]
                observations.append(
                    MethodologyObservation(
                        run_id=run["run_id"],
                        task_id=run["run_id"],
                        methodology_id=run["methodology_id"],
                        version=run["version"],
                        started_at=datetime.fromisoformat(
                            run["started_at"] or run["created_at"]
                        ),
                        finished_at=datetime.fromisoformat(run["finished_at"])
                        if run["finished_at"]
                        else None,
                        status=run["status"],
                        review_iterations=None,
                        first_review_passed=None,
                        required_rework=None,
                        findings=(),
                        gates_passed=all(
                            validations.get(gate) is True for gate in required_gates
                        )
                        if required_gates and run["methodology_id"] != "tdd-adversarial"
                        else None,
                        human_interventions=interventions,
                        agent_invocations=invocations,
                        approval_at=datetime.fromisoformat(approval["decided_at"])
                        if approval
                        else None,
                    )
                )
        return observations
