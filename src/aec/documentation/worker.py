"""ACP-driven immutable documentation worker."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

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
from aec.security.identities import Identity, Role

from .collector import DocumentationCollector
from .renderer import change_summary, execution_report, qa_summary


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
            raise KeyError(document_id)
        ref = ArtifactRef.model_validate_json(row["artifact_ref"])
        content = str(row["content"])
        if hashlib.sha256(content.encode()).hexdigest() != row[
            "sha256"
        ] or not self.collector.artifact_intact(ref):
            raise ValueError("document integrity failed")
        return content

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
        evidence = self.collector.collect(request.run_id, request.task_id)
        if evidence.status == "APPROVED":
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
        source_refs = list(evidence.source_refs)
        if any(not self.collector.artifact_intact(ref) for ref in source_refs):
            raise ValueError("source artifact is missing or has an invalid hash")
        for ref in request.artifact_refs:
            if ref not in source_refs or not self.collector.artifact_intact(ref):
                raise ValueError("requested artifact is missing or unverified")
        document_id = hashlib.sha256(message.message_id.encode()).hexdigest()
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
            "VALID_WITH_WARNINGS" if evidence.warnings else "VALID"
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
                        json.dumps(evidence.warnings),
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
            warnings=list(evidence.warnings),
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
        "execution-report", "qa-summary", "change-summary"
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
