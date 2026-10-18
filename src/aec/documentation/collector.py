"""Read and normalize typed engineering evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from aec.communication.broker import Broker
from aec.communication.schemas import ArtifactRef, Message
from aec.engineering.gates import GateResult
from aec.engineering.models import ImplementationResult, QAReviewResult


@dataclass(frozen=True)
class IterationEvidence:
    number: int
    status: str
    implementation: ImplementationResult | None
    review: QAReviewResult | None
    gates: tuple[GateResult, ...]


@dataclass(frozen=True)
class ExecutionEvidence:
    run_id: str
    task_id: str
    specification: str
    criteria: tuple[str, ...]
    status: str
    base_commit: str
    head_commit: str
    iterations: tuple[IterationEvidence, ...]
    messages: tuple[Message, ...]
    source_refs: tuple[ArtifactRef, ...]
    methodologies: tuple[str, ...]
    approvals: tuple[tuple[str, str, str | None], ...]
    warnings: tuple[str, ...]


class DocumentationCollector:
    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    def artifact_intact(self, ref: ArtifactRef) -> bool:
        source_run = urlsplit(ref.uri).netloc
        if ref.uri != f"artifact://{source_run}/{ref.artifact_id}":
            return False
        path = (
            Path(self.broker.db.path).resolve().parent
            / "artifacts"
            / hashlib.sha256(source_run.encode()).hexdigest()
            / ref.artifact_id
        )
        return (
            path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == ref.sha256
        )

    def collect(self, run_id: str, task_id: str) -> ExecutionEvidence:
        with self.broker.db.connect() as db:
            row = db.execute(
                "SELECT * FROM engineering_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["task_id"] != task_id:
                raise ValueError("task_id does not match engineering run")
            raw_iterations = db.execute(
                "SELECT * FROM engineering_iterations WHERE run_id=? ORDER BY number",
                (run_id,),
            ).fetchall()
            approvals = db.execute(
                "SELECT a.run_id,a.status,a.decided_by FROM workflow_approvals a "
                "WHERE a.run_id=? ORDER BY a.step_id",
                (f"{run_id}-approval",),
            ).fetchall()
            workflows = [
                db.execute(
                    "SELECT definition FROM workflow_runs WHERE run_id=?",
                    (f"{run_id}-i{item['number']}-{stage}",),
                ).fetchone()
                for item in raw_iterations
                for stage in ("implement", "review")
            ]
        iterations = tuple(
            IterationEvidence(
                item["number"],
                item["status"],
                ImplementationResult.model_validate_json(item["implementation"])
                if item["implementation"]
                else None,
                QAReviewResult.model_validate_json(item["review"])
                if item["review"]
                else None,
                tuple(
                    GateResult.model_validate_json(json.dumps(gate))
                    for gate in json.loads(item["gates"])
                )
                if item["gates"]
                else (),
            )
            for item in raw_iterations
        )
        messages = list(self.broker.get_history(run_id))
        for item in iterations:
            for stage in ("implement", "review"):
                messages.extend(
                    self.broker.get_history(f"{run_id}-i{item.number}-{stage}")
                )
        messages.extend(self.broker.get_history(f"{run_id}-approval"))
        messages.sort(key=lambda message: (message.created_at, message.message_id))
        refs = [ref for message in messages for ref in message.artifact_refs]
        for item in iterations:
            if item.implementation:
                refs.extend(item.implementation.artifact_refs)
            if item.review:
                refs.extend(item.review.evidence_refs)
                refs.extend(
                    ref
                    for finding in item.review.findings
                    for ref in finding.evidence_refs
                )
        refs_by_uri: dict[str, ArtifactRef] = {}
        for ref in refs:
            previous = refs_by_uri.get(ref.uri)
            if previous is not None and previous != ref:
                raise ValueError("conflicting artifact references")
            refs_by_uri[ref.uri] = ref
        warnings = [
            f"Artefato indisponível ou hash inválido: {ref.uri}"
            for ref in refs
            if not self.artifact_intact(ref)
        ]
        if row["status"] == "APPROVED" and not any(
            approval["status"] == "APPROVED" and approval["decided_by"]
            for approval in approvals
        ):
            warnings.append("Estado aprovado sem aprovação humana registrada")
        if not iterations:
            warnings.append("Nenhuma iteração registrada")
        methodologies = tuple(
            sorted(
                {
                    f"{definition['id']}@{definition['version']}"
                    for workflow in workflows
                    if workflow is not None
                    for definition in [json.loads(workflow["definition"])]
                    if definition.get("metadata", {}).get("engineering_run_id")
                    == run_id
                }
            )
        )
        return ExecutionEvidence(
            run_id,
            task_id,
            row["specification"],
            tuple(json.loads(row["criteria"])),
            row["status"],
            row["base_commit"],
            row["head_commit"],
            iterations,
            tuple(messages),
            tuple(refs_by_uri.values()),
            methodologies,
            tuple(
                (item["run_id"], item["status"], item["decided_by"])
                for item in approvals
            ),
            tuple(warnings),
        )
