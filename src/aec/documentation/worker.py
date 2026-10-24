"""ACP-driven immutable documentation worker."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, cast

from aec.communication.broker import Broker
from aec.communication.errors import ConflictError
from aec.communication.schemas import (
    ArtifactRef,
    DocumentationFailure,
    DocumentationRequest,
    DocumentationResult,
    Message,
    MessageType,
)
from aec.methodologies.arena import arena_comparison
from aec.methodologies.static_audit import audit_snapshot
from aec.methodology.contracts import MethodologyEvaluation, ProposalStatus
from aec.methodology.governance import MethodologyRegistry, ProposalStore
from aec.security.identities import Identity, Role
from aec.workflows.loader import definition_hash
from aec.workflows.models import WorkflowDefinition
from aec.workflows.repository import WorkflowRepository

from .collector import DocumentationCollector
from .renderer import (
    change_summary,
    execution_report,
    methodology_document,
    qa_summary,
    safe,
)

WORKFLOW_REPORTS = {
    "implementation-arena": "arena-report",
    "tdd-adversarial": "tdd-report",
    "security-audit": "security-audit-report",
    "decision-adversarial": "decision-report",
}


class DocumentationWorker:
    def __init__(self, broker: Broker, identity: Identity) -> None:
        broker._identity(identity)
        if identity.role != Role.DOCUMENTATION:
            raise PermissionError("documentation identity required")
        self.broker = broker
        self.identity = identity
        self.collector = DocumentationCollector(broker)

    def get_document(self, document_id: str) -> str:
        with self.broker.db.connect() as db:
            row = db.execute(
                "SELECT content,sha256,artifact_ref FROM documentation_records "
                "WHERE document_id=?",
                (document_id,),
            ).fetchone()
            if row is None:
                row = db.execute(
                    "SELECT content,sha256,artifact_ref FROM methodology_documents "
                    "WHERE document_id=?",
                    (document_id,),
                ).fetchone()
        if row is None:
            raise KeyError(document_id)
        ref = ArtifactRef.model_validate_json(row["artifact_ref"])
        content = str(row["content"])
        if hashlib.sha256(content.encode()).hexdigest() != row[
            "sha256"
        ] or not self.collector.artifact_intact(ref):
            raise ValueError("document integrity failed")
        return content

    def _workflow_report(self, run_id: str, document_type: str) -> str:
        with self.broker.db.connect() as db:
            run = db.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            binding = db.execute(
                "SELECT * FROM methodology_run_bindings WHERE run_id=?", (run_id,)
            ).fetchone()
            steps = db.execute(
                "SELECT * FROM workflow_steps WHERE run_id=? ORDER BY step_id",
                (run_id,),
            ).fetchall()
            approvals = db.execute(
                "SELECT step_id,status,decided_by FROM workflow_approvals "
                "WHERE run_id=? ORDER BY step_id",
                (run_id,),
            ).fetchall()
            published = (
                db.execute(
                    "SELECT sha256 FROM methodology_versions WHERE methodology_id=? "
                    "AND version=?",
                    (binding["methodology_id"], binding["version"]),
                ).fetchone()
                if binding
                else None
            )
        if run is None or binding is None or published is None:
            raise ValueError("published workflow run required")
        definition = WorkflowDefinition.model_validate_json(run["definition"])
        registered = MethodologyRegistry(self.broker.db).get(
            binding["methodology_id"], binding["version"]
        )
        if (
            definition_hash(definition) != run["definition_hash"]
            or definition_hash(registered.definition) != run["definition_hash"]
            or published["sha256"] != binding["sha256"]
            or WORKFLOW_REPORTS.get(binding["methodology_id"]) != document_type
        ):
            raise ValueError("workflow report source mismatch")
        by_id = {step.id: step for step in definition.steps}
        lines = [
            f"# {safe(document_type)}",
            "",
            f"Execução: {safe(run_id)}",
            "Metodologia: "
            f"{safe(binding['methodology_id'])}@{safe(binding['version'])}",
            f"Definição SHA-256: {safe(run['definition_hash'])}",
            f"Estado: {safe(run['status'])}",
            "",
        ]
        task_input = WorkflowRepository(self.broker.db).task_input(run_id)
        if task_input is not None:
            lines.extend(
                [
                    "## Entrada da tarefa",
                    "",
                    f"Requisitos: {safe(task_input.requirements)}",
                    "Critérios de aceite: "
                    + safe(json.dumps(task_input.acceptance_criteria)),
                    "Referências de artefatos: "
                    + safe(
                        json.dumps(
                            [
                                ref.model_dump(mode="json")
                                for ref in task_input.artifact_refs
                            ],
                            sort_keys=True,
                        )
                    ),
                    "",
                ]
            )
        lines.extend(["## Etapas e evidências", ""])
        for step in steps:
            kind = by_id[step["step_id"]].type
            result = json.loads(step["result"]) if step["result"] else {}
            evidence = (
                f"resultado do validador={result.get('passed', 'indisponível')}"
                if kind == "validation"
                else "saída declarada pelo agente="
                + safe(result.get("output", "indisponível"))
                if kind == "agent"
                else "decisão humana pendente ou registrada abaixo"
            )
            lines.append(
                f"- {safe(step['step_id'])}: {safe(step['status'])}; {evidence}"
            )
        lines.extend(["", "## Aprovações", ""])
        lines.extend(
            f"- {safe(item['step_id'])}: {safe(item['status'])} por "
            f"{safe(item['decided_by'] or 'ninguém')}"
            for item in approvals
        )
        lines.extend(["", "Sem aprovação registrada"] if not approvals else [])
        if definition.metadata.get("approval_binding") == "audit_artifact":
            with self.broker.db.connect() as db:
                snapshot_exists = (
                    db.execute(
                        "SELECT 1 FROM audit_artifact_snapshots WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    is not None
                )
            if not snapshot_exists and run["status"] == "FAILED":
                lines.extend(["", "## Artefato validado", "", "Não produzido."])
                return "\n".join(lines) + "\n"
            manifest, _, manifest_digest = audit_snapshot(self.broker.db, run_id)
            if (
                manifest["definition_hash"] != run["definition_hash"]
                or manifest["methodology_id"] != binding["methodology_id"]
                or manifest["version"] != binding["version"]
                or manifest["methodology_sha256"] != binding["sha256"]
            ):
                raise ValueError("audit snapshot and methodology do not match")
            approval_binding = WorkflowRepository(self.broker.db).approval_binding(
                run_id, "approval"
            )
            if approval_binding is not None and approval_binding != (
                manifest_digest,
                run["definition_hash"],
                binding["methodology_id"],
                binding["version"],
                binding["sha256"],
            ):
                raise ValueError("audit approval does not match the snapshot")
            lines.extend(
                [
                    "",
                    "## Artefato validado",
                    "",
                    f"Manifest SHA-256: {safe(manifest_digest)}",
                    f"Arquivo: {safe(manifest['path'])}",
                    f"Arquivo SHA-256: {safe(manifest['content_sha256'])}",
                    f"Commit validado: {safe(manifest['commit'])}",
                    "Aprovação vinculada: "
                    + safe(approval_binding[0] if approval_binding else "pendente"),
                ]
            )
        if document_type == "arena-report":
            comparison = arena_comparison(self.broker.db, run_id)
            lines.extend(
                [
                    "",
                    "## Comparação",
                    "",
                    f"Especificação: {safe(comparison['specification'])}",
                    f"Critérios: {safe(json.dumps(comparison['criteria']))}",
                    f"Commit-base: {safe(comparison['base_commit'])}",
                    f"Vencedor: {safe(comparison['winner'] or 'nenhum')}",
                    f"Estado do QA: {safe(comparison['qa_status'] or 'não executado')}",
                    f"QA aprovado: {safe(comparison['qa_passed'])}",
                    "",
                ]
            )
            for candidate in cast(list[dict[str, object]], comparison["candidates"]):
                lines.append(
                    f"- {safe(candidate['agent'])}: commit "
                    f"{safe(candidate['commit'] or 'nenhum')}; "
                    f"gates {safe(json.dumps(candidate['gates'], sort_keys=True))}; "
                    f"erro {safe(candidate['error'] or 'nenhum')}"
                )
                if candidate.get("benchmark"):
                    lines.append(
                        "  Tempo e memória por amostra: "
                        + safe(json.dumps(candidate["benchmark"], sort_keys=True))
                    )
                if candidate.get("benchmark_error"):
                    lines.append(
                        "  Falha de medição: " + safe(candidate["benchmark_error"])
                    )
            if comparison.get("selection_rule"):
                lines.append("Regra de seleção: " + safe(comparison["selection_rule"]))
        return "\n".join(lines) + "\n"

    def create_methodology_document(
        self,
        proposal_id: str,
        kind: Literal["rfc", "evaluation", "decision", "publication"],
    ) -> str:
        """Render only persisted methodology facts; return the immutable document ID."""
        proposal, status, proposal_sha = ProposalStore(self.broker.db).get(proposal_id)
        with self.broker.db.connect() as db:
            row = db.execute(
                "SELECT evaluation,approval_by,approval_at,approval_candidate_sha256 "
                "FROM methodology_proposals "
                "WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            publication = db.execute(
                "SELECT sha256,published_at,approved_by FROM methodology_versions "
                "WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        fields = [
            ("Proposta", proposal_id),
            ("Metodologia", proposal.methodology_id),
            ("Versões", f"{proposal.base_version} -> {proposal.proposed_version}"),
            ("Hash da proposta", proposal_sha),
        ]
        if kind == "rfc":
            fields.extend(
                [
                    ("Problema", proposal.problem),
                    ("Hipótese", proposal.hypothesis),
                    ("Evidências", ", ".join(proposal.evidence_refs)),
                    (
                        "Mudança",
                        ", ".join(
                            f"{change.operation}: {change.gate} {change.position}"
                            for change in proposal.changes
                        ),
                    ),
                    (
                        "Efeito esperado",
                        f"{proposal.expected_metric}: {proposal.expected_direction}",
                    ),
                    ("Riscos", proposal.risks),
                    ("Validação", proposal.validation_criteria),
                    ("Reversão", proposal.rollback_plan),
                ]
            )
            if proposal.kind == "METHODOLOGY_REVISION":
                fields.extend(
                    [
                        ("Tipo", "Revisão completa"),
                        ("Digest da candidata", str(proposal.candidate_sha256)),
                    ]
                )
        elif kind == "evaluation":
            if row is None or not row["evaluation"]:
                raise ValueError("evaluation has not been recorded")
            evaluation = MethodologyEvaluation.model_validate_json(row["evaluation"])
            if evaluation.proposal_sha256 != proposal_sha:
                raise ValueError("evaluation proposal hash mismatch")
            fields.extend(
                [
                    ("Resultado", evaluation.verdict.value),
                    ("Verificações", "; ".join(evaluation.checks)),
                    ("Limitações", "; ".join(evaluation.limitations)),
                    (
                        "Pares",
                        "; ".join(
                            f"{pair.task_id}: {pair.baseline} -> {pair.candidate}"
                            for pair in evaluation.pairs
                        ),
                    ),
                ]
            )
            if proposal.kind == "METHODOLOGY_REVISION":
                fields.extend(
                    [
                        ("Digest da candidata", str(evaluation.candidate_sha256)),
                        ("Digest das evidências", str(evaluation.evidence_sha256)),
                        (
                            "Diff semântico",
                            "; ".join(
                                f"{change.category}:{change.subject} "
                                f"{change.before} -> {change.after}"
                                for change in evaluation.semantic_diff
                            ),
                        ),
                    ]
                )
        elif kind == "decision":
            if (
                row is None
                or not row["approval_by"]
                or not row["approval_at"]
                or status
                not in {
                    ProposalStatus.APPROVED,
                    ProposalStatus.REJECTED,
                    ProposalStatus.CHANGES_REQUESTED,
                    ProposalStatus.PUBLISHED,
                }
            ):
                raise ValueError("human decision has not been recorded")
            fields.extend(
                [
                    (
                        "Decisão",
                        "APPROVED"
                        if status == ProposalStatus.PUBLISHED
                        else status.value,
                    ),
                    ("Identidade", str(row["approval_by"])),
                    ("Data", str(row["approval_at"])),
                ]
            )
            if proposal.kind == "METHODOLOGY_REVISION":
                fields.append(
                    ("Digest aprovado", str(row["approval_candidate_sha256"]))
                )
        elif kind == "publication":
            if status != ProposalStatus.PUBLISHED or publication is None:
                raise ValueError("methodology has not been published")
            fields.extend(
                [
                    ("Hash da versão", str(publication["sha256"])),
                    ("Aprovador", str(publication["approved_by"])),
                    ("Publicada em", str(publication["published_at"])),
                ]
            )
        else:
            raise ValueError("unsupported methodology document type")
        document_id = hashlib.sha256(f"{proposal_id}:{kind}".encode()).hexdigest()
        content = methodology_document(kind.upper(), fields)
        digest = hashlib.sha256(content.encode()).hexdigest()
        artifact_id = f"{document_id}.md"
        ref = ArtifactRef(
            artifact_id=artifact_id,
            kind="documentation/markdown",
            uri=f"artifact://{proposal_id}/{artifact_id}",
            sha256=digest,
            created_by=self.identity.agent_id,
        )
        artifact_root = Path(self.broker.db.path).resolve().parent / "artifacts"
        directory = artifact_root / hashlib.sha256(proposal_id.encode()).hexdigest()
        if artifact_root.is_symlink() or directory.is_symlink():
            raise PermissionError("artifact directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / artifact_id
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(content)
        except FileExistsError:
            if not self.collector.artifact_intact(ref):
                raise ValueError("historical methodology document differs") from None
        with self.broker.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT sha256,content FROM methodology_documents WHERE document_id=?",
                (document_id,),
            ).fetchone()
            if prior is None:
                db.execute(
                    "INSERT INTO methodology_documents VALUES (?,?,?,?,?,?,?)",
                    (
                        document_id,
                        proposal_id,
                        kind,
                        content,
                        digest,
                        ref.model_dump_json(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            elif prior["sha256"] != digest or prior["content"] != content:
                raise ValueError(
                    "historical methodology document cannot be overwritten"
                )
        return document_id

    def process_next(self) -> DocumentationResult | None:
        delivery = self.broker.claim(self.identity.agent_id, self.identity)
        if delivery is None:
            return None
        message = delivery.message
        if message.type != MessageType.DOCUMENTATION_REQUEST:
            self.broker.nack(
                delivery,
                "unsupported documentation message",
                self.identity,
                permanent=True,
            )
            return None
        try:
            result = self._produce(message)
            self._publish(message, MessageType.DOCUMENTATION_GENERATED, result)
            self.broker.ack(delivery, self.identity)
            return result
        except (KeyError, ValueError, PermissionError) as exc:
            self._publish(
                message,
                MessageType.DOCUMENTATION_FAILED,
                DocumentationFailure(
                    request_message_id=message.message_id, reason=str(exc)
                ),
            )
            self.broker.ack(delivery, self.identity)
            return None
        except Exception as exc:
            self.broker.nack(delivery, type(exc).__name__, self.identity)
            raise

    def _publish(
        self,
        request: Message,
        kind: MessageType,
        payload: DocumentationResult | DocumentationFailure,
    ) -> None:
        refs = (
            [payload.generated_artifact_ref]
            if isinstance(payload, DocumentationResult)
            else []
        )
        self.broker.publish(
            Message(
                protocol="ACP",
                version="0.1",
                message_id=f"documentation:{request.message_id}:{kind.value}",
                run_id=request.run_id,
                task_id=request.task_id,
                sender=self.identity.agent_id,
                recipient=request.sender,
                type=kind,
                iteration=request.iteration,
                created_at=payload.timestamp
                if isinstance(payload, DocumentationResult)
                else request.created_at,
                payload=payload,
                correlation_id=request.correlation_id,
                causation_id=request.message_id,
                artifact_refs=refs,
            ),
            self.identity,
        )

    def _produce(self, message: Message) -> DocumentationResult:
        request = message.payload
        if not isinstance(request, DocumentationRequest):
            raise ValueError("documentation payload required")
        if (
            request.run_id,
            request.task_id,
            request.requested_by,
            request.correlation_id,
        ) != (message.run_id, message.task_id, message.sender, message.correlation_id):
            raise ValueError("request does not match authenticated envelope")
        if request.target_scope != "aec-run":
            raise PermissionError("only AEC run records are authorized")
        if request.documentation_type not in {
            "execution-report",
            "qa-summary",
            "change-summary",
            *WORKFLOW_REPORTS.values(),
        }:
            raise ValueError("document type requires supervised authoring")
        with self.broker.db.connect() as db:
            prior = db.execute(
                "SELECT * FROM documentation_records WHERE request_message_id=?",
                (message.message_id,),
            ).fetchone()
        if prior is not None:
            ref = ArtifactRef.model_validate_json(prior["artifact_ref"])
            self.get_document(prior["document_id"])
            return DocumentationResult(
                document_id=prior["document_id"],
                document_type=prior["document_type"],
                generated_artifact_ref=ref,
                source_refs=[
                    ArtifactRef.model_validate(item)
                    for item in json.loads(prior["source_refs"])
                ],
                validation_status=prior["validation_status"],
                warnings=json.loads(prior["warnings"]),
                timestamp=datetime.fromisoformat(prior["created_at"]),
            )
        workflow_report = request.documentation_type in WORKFLOW_REPORTS.values()
        evidence = (
            None
            if workflow_report
            else self.collector.collect(request.run_id, request.task_id)
        )
        if evidence is not None and evidence.status == "APPROVED":
            final = evidence.iterations[-1] if evidence.iterations else None
            if (
                not any(
                    status == "APPROVED" and actor
                    for _, status, actor in evidence.approvals
                )
                or final is None
                or final.implementation is None
                or final.implementation.result_commit != evidence.head_commit
                or final.review is None
                or final.review.review_status != "PASSED"
                or not final.gates
                or any(gate.status != "PASSED" for gate in final.gates)
            ):
                raise ValueError("approved run lacks verified final evidence")
        source_refs = [] if evidence is None else list(evidence.source_refs)
        if any(not self.collector.artifact_intact(ref) for ref in source_refs):
            raise ValueError("source artifact is missing or has an invalid hash")
        for ref in request.artifact_refs:
            if ref not in source_refs or not self.collector.artifact_intact(ref):
                raise ValueError("requested artifact is missing or unverified")
        document_id = hashlib.sha256(message.message_id.encode()).hexdigest()
        if workflow_report:
            content = self._workflow_report(request.run_id, request.documentation_type)
        else:
            assert evidence is not None
            content = (
                execution_report(evidence, document_id)
                if request.documentation_type == "execution-report"
                else {
                    "qa-summary": qa_summary,
                    "change-summary": change_summary,
                }[request.documentation_type](evidence)
            )
        artifact_id = f"{document_id}.md"
        digest = hashlib.sha256(content.encode()).hexdigest()
        ref = ArtifactRef(
            artifact_id=artifact_id,
            kind="documentation/markdown",
            uri=f"artifact://{message.run_id}/{artifact_id}",
            sha256=digest,
            created_by=self.identity.agent_id,
        )
        artifact_root = Path(self.broker.db.path).resolve().parent / "artifacts"
        directory = artifact_root / hashlib.sha256(message.run_id.encode()).hexdigest()
        if artifact_root.is_symlink() or directory.is_symlink():
            raise PermissionError("artifact directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / artifact_id
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(content)
        except FileExistsError:
            if not self.collector.artifact_intact(ref):
                raise ValueError(
                    "historical document path has different content"
                ) from None
        timestamp = datetime.now(timezone.utc)
        validation: Literal["VALID", "VALID_WITH_WARNINGS", "INVALID"] = (
            "VALID_WITH_WARNINGS"
            if evidence is not None and evidence.warnings
            else "VALID"
        )
        with self.broker.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM documentation_records WHERE request_message_id=?",
                (message.message_id,),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO documentation_records VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        document_id,
                        message.message_id,
                        message.run_id,
                        message.task_id,
                        request.documentation_type,
                        content,
                        digest,
                        ref.model_dump_json(),
                        json.dumps(
                            [item.model_dump(mode="json") for item in source_refs]
                        ),
                        validation,
                        json.dumps(evidence.warnings if evidence is not None else []),
                        timestamp.isoformat(),
                    ),
                )
            elif row["sha256"] != digest or row["content"] != content:
                raise ValueError("historical document cannot be overwritten")
            else:
                timestamp = datetime.fromisoformat(row["created_at"])
                validation = row["validation_status"]
        return DocumentationResult(
            document_id=document_id,
            document_type=request.documentation_type,
            generated_artifact_ref=ref,
            source_refs=source_refs,
            validation_status=validation,
            warnings=list(evidence.warnings) if evidence is not None else [],
            timestamp=timestamp,
        )


def request_documentation(
    broker: Broker,
    orchestrator: Identity,
    worker: Identity,
    run_id: str,
    task_id: str,
    *,
    documentation_type: Literal[
        "execution-report",
        "qa-summary",
        "change-summary",
        "arena-report",
        "tdd-report",
        "security-audit-report",
        "decision-report",
    ] = "execution-report",
    correlation_id: str | None = None,
    artifact_refs: list[ArtifactRef] | None = None,
) -> Message:
    """Enqueue an AEC run report from trusted orchestration code."""
    broker._identity(orchestrator)
    broker._identity(worker)
    if orchestrator.role != Role.ORCHESTRATOR or worker.role != Role.DOCUMENTATION:
        raise PermissionError("orchestrator and documentation identities required")
    correlation = correlation_id or f"documentation:{run_id}"
    refs = artifact_refs or []
    identifier = hashlib.sha256(
        json.dumps(
            [
                run_id,
                task_id,
                documentation_type,
                correlation,
                [ref.model_dump(mode="json") for ref in refs],
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    message = Message(
        protocol="ACP",
        version="0.1",
        message_id=f"documentation-request:{identifier}",
        run_id=run_id,
        task_id=task_id,
        sender=orchestrator.agent_id,
        recipient=worker.agent_id,
        type=MessageType.DOCUMENTATION_REQUEST,
        iteration=0,
        created_at=datetime.now(timezone.utc),
        payload=DocumentationRequest(
            run_id=run_id,
            task_id=task_id,
            documentation_type=documentation_type,
            target_scope="aec-run",
            requested_by=orchestrator.agent_id,
            correlation_id=correlation,
            artifact_refs=refs,
        ),
        correlation_id=correlation,
    )
    prior = broker.get_message(message.message_id)
    if prior is not None:
        return prior
    try:
        return broker.publish(message, orchestrator)
    except ConflictError:
        prior = broker.get_message(message.message_id)
        if prior is None or prior.payload != message.payload:
            raise
        return prior


def workflow_documentation_validator(
    broker: Broker,
    orchestrator: Identity,
    worker: DocumentationWorker,
) -> Callable[[dict[str, str]], bool]:
    """Register this callable as a Workflow Engine validation step."""

    def validate(inputs: dict[str, str]) -> bool:
        request = request_documentation(
            broker,
            orchestrator,
            worker.identity,
            inputs["engineering_run_id"],
            inputs["task_id"],
            correlation_id=inputs.get("correlation_id"),
        )
        prefix = f"documentation:{request.message_id}:"
        generated = prefix + MessageType.DOCUMENTATION_GENERATED.value
        failed = prefix + MessageType.DOCUMENTATION_FAILED.value
        with broker.db.connect() as db:
            pending = db.execute(
                "SELECT count(*) FROM deliveries WHERE recipient=? "
                "AND state IN ('PENDING','RETRY_PENDING')",
                (worker.identity.agent_id,),
            ).fetchone()[0]
        for _ in range(pending):
            if broker.get_message(generated):
                return True
            if broker.get_message(failed):
                return False
            worker.process_next()
        return broker.get_message(generated) is not None

    return validate
