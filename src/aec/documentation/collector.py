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
from aec.execution.models import ExecutionResult
from aec.workflows.loader import definition_hash
from aec.workflows.models import WorkflowDefinition


@dataclass(frozen=True)
class IterationEvidence:
    number: int
    status: str
    implementation: ImplementationResult | None
    review: QAReviewResult | None
    gates: tuple[GateResult, ...]


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    agent_id: str
    status: str
    exit_code: int | None
    artifact_refs: tuple[ArtifactRef, ...]


@dataclass(frozen=True)
class WorkflowEvent:
    run_id: str
    step_id: str | None
    kind: str
    created_at: str


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
    executions: tuple[ExecutionRecord, ...]
    workflow_events: tuple[WorkflowEvent, ...]
    source_refs: tuple[ArtifactRef, ...]
    methodologies: tuple[str, ...]
    approvals: tuple[tuple[str, str, str | None], ...]
    adrs: tuple[tuple[str, int, str, str], ...]
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
            stage_ids = [
                f"{run_id}-i{item['number']}-{stage}"
                for item in raw_iterations
                for stage in ("implement", "review")
            ]
            related_ids = [run_id, *stage_ids, f"{run_id}-approval"]
            placeholders = ",".join("?" for _ in related_ids)
            raw_executions = db.execute(
                "SELECT execution_id,agent_id,status,result FROM executions "
                f"WHERE run_id IN ({placeholders}) ORDER BY created_at,execution_id",
                related_ids,
            ).fetchall()
            raw_events = db.execute(
                "SELECT run_id,step_id,kind,created_at FROM workflow_events "
                f"WHERE run_id IN ({placeholders}) ORDER BY created_at,event_id",
                related_ids,
            ).fetchall()
            approvals = db.execute(
                "SELECT a.run_id,a.status,a.decided_by FROM workflow_approvals a "
                "WHERE a.run_id=? ORDER BY a.step_id",
                (f"{run_id}-approval",),
            ).fetchall()
            adrs = db.execute(
                "SELECT adr_id,revision,status,content,sha256 FROM adr_records "
                "WHERE run_id=? ORDER BY adr_id,revision",
                (run_id,),
            ).fetchall()
            workflows = [
                db.execute(
                    "SELECT definition,definition_hash FROM workflow_runs "
                    "WHERE run_id=?",
                    (workflow_id,),
                ).fetchone()
                for workflow_id in stage_ids
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
        executions = []
        for item in raw_executions:
            result = (
                ExecutionResult.model_validate_json(item["result"])
                if item["result"] else None
            )
            if result is not None and (
                result.execution_id != item["execution_id"]
                or result.agent_id != item["agent_id"]
                or result.status.value != item["status"]
            ):
                raise ValueError("execution result conflicts with persisted state")
            executions.append(
                ExecutionRecord(
                    item["execution_id"], item["agent_id"], item["status"],
                    result.exit_code if result else None,
                    tuple(result.artifact_refs) if result else (),
                )
            )
        events = tuple(
            WorkflowEvent(
                item["run_id"], item["step_id"], item["kind"], item["created_at"]
            )
            for item in raw_events
        )
        refs = [ref for message in messages for ref in message.artifact_refs]
        refs.extend(ref for execution in executions for ref in execution.artifact_refs)
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
        versions = set()
        for workflow in workflows:
            if workflow is None:
                continue
            definition = WorkflowDefinition.model_validate_json(workflow["definition"])
            if definition_hash(definition) != workflow["definition_hash"]:
                raise ValueError("workflow definition hash mismatch")
            if definition.metadata.get("engineering_run_id") == run_id:
                versions.add(f"{definition.id}@{definition.version}")
        methodologies = tuple(sorted(versions))
        if any(
            hashlib.sha256(adr["content"].encode()).hexdigest() != adr["sha256"]
            for adr in adrs
        ):
            raise ValueError("ADR content hash mismatch")
        return ExecutionEvidence(
            run_id=run_id,
            task_id=task_id,
            specification=row["specification"],
            criteria=tuple(json.loads(row["criteria"])),
            status=row["status"],
            base_commit=row["base_commit"],
            head_commit=row["head_commit"],
            iterations=iterations,
            messages=tuple(messages),
            executions=tuple(executions),
            workflow_events=events,
            source_refs=tuple(refs_by_uri.values()),
            methodologies=methodologies,
            approvals=tuple(
                (item["run_id"], item["status"], item["decided_by"])
                for item in approvals
            ),
            adrs=tuple(
                (item["adr_id"], item["revision"], item["status"], item["sha256"])
                for item in adrs
            ),
            warnings=tuple(warnings),
        )
