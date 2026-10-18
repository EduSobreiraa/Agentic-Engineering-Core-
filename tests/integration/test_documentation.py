from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import (
    ArtifactRef,
    ArtifactSubmitted,
    DocumentationResult,
    Message,
    MessageType,
)
from aec.documentation.adr import ADR, render_adr
from aec.documentation.renderer import execution_report, technical_documentation
from aec.documentation.worker import (
    DocumentationWorker,
    request_documentation,
    workflow_documentation_validator,
)
from aec.security.identities import Identity, Role


def seed(tmp_path: Path) -> tuple[Broker, dict[Role, Identity], ArtifactRef]:
    broker = Broker(tmp_path / "aec.sqlite", lease_seconds=1)
    ids = {
        role: broker.register(role.value, role)
        for role in (Role.ORCHESTRATOR, Role.DOCUMENTATION, Role.IMPLEMENTER, Role.QA)
    }
    content = b"implementation evidence"
    artifact_id = "impl-evidence"
    path = tmp_path / "artifacts" / hashlib.sha256(b"run-1").hexdigest() / artifact_id
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    ref = ArtifactRef(
        artifact_id=artifact_id,
        kind="execution-output",
        uri="artifact://run-1/impl-evidence",
        sha256=hashlib.sha256(content).hexdigest(),
        created_by=ids[Role.IMPLEMENTER].agent_id,
    )
    broker.publish(
        Message(
            protocol="ACP",
            version="0.1",
            message_id="implementation-evidence",
            run_id="run-1",
            task_id="task-1",
            sender=ids[Role.IMPLEMENTER].agent_id,
            recipient=ids[Role.ORCHESTRATOR].agent_id,
            type=MessageType.ARTIFACT_SUBMITTED,
            iteration=1,
            created_at=datetime.now(timezone.utc),
            payload=ArtifactSubmitted(
                summary="implementation", artifact_id=artifact_id
            ),
            artifact_refs=[ref],
        ),
        ids[Role.IMPLEMENTER],
    )
    first = {
        "task_id": "task-1",
        "iteration": 1,
        "status": "SUCCEEDED",
        "base_commit": "a" * 40,
        "result_commit": "b" * 40,
        "changed_files": ["src/module.py"],
        "artifact_refs": [ref.model_dump(mode="json")],
        "test_results": ["agent claimed success"],
        "implementation_summary": "first attempt",
    }
    second = {
        **first,
        "iteration": 2,
        "base_commit": "b" * 40,
        "result_commit": "c" * 40,
        "implementation_summary": "fixed issue",
    }
    review = {
        "task_id": "task-1",
        "iteration": 1,
        "reviewed_commit": "b" * 40,
        "review_status": "FAILED",
        "findings": [
            {
                "finding_id": "F-1",
                "severity": "HIGH",
                "category": "bug",
                "title": "broken",
                "description": "unsafe behavior",
                "status": "REPRODUCED",
                "reproduction_steps": ["run example"],
            }
        ],
    }
    final_review = {
        "task_id": "task-1",
        "iteration": 2,
        "reviewed_commit": "c" * 40,
        "review_status": "PASSED",
        "findings": [],
    }
    gate = {
        "gate_id": "pytest",
        "validator": "pytest",
        "exit_code": 0,
        "status": "PASSED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence": "1 passed",
    }
    with broker.db.connect() as db:
        db.execute(
            "INSERT INTO engineering_runs "
            "(run_id,task_id,specification,criteria,allowed_paths,repository_path,"
            "worktree_path,base_commit,head_commit,iteration,max_iterations,"
            "max_duration_seconds,gate_config,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "run-1",
                "task-1",
                "Fix parser",
                json.dumps(["handles missing value"]),
                json.dumps(["src"]),
                str(tmp_path),
                str(tmp_path),
                "a" * 40,
                "c" * 40,
                2,
                3,
                120,
                "[]",
                "APPROVED",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        db.execute(
            "INSERT INTO engineering_iterations VALUES (?,?,?,?,?,?)",
            (
                "run-1",
                1,
                "REWORK",
                json.dumps(first),
                json.dumps(review),
                "[]",
            ),
        )
        db.execute(
            "INSERT INTO engineering_iterations VALUES (?,?,?,?,?,?)",
            (
                "run-1",
                2,
                "VALIDATED",
                json.dumps(second),
                json.dumps(final_review),
                json.dumps([gate]),
            ),
        )
        db.execute(
            "INSERT INTO workflow_runs"
            "(run_id,definition,definition_hash,status,created_at) "
            "VALUES (?,?,?,?,?)",
            (
                "run-1-approval",
                "{}",
                "hash",
                "COMPLETED",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        db.execute(
            "INSERT INTO workflow_steps(run_id,step_id,status) VALUES (?,?,?)",
            (
                "run-1-approval",
                "approval",
                "SUCCEEDED",
            ),
        )
        db.execute(
            "INSERT INTO workflow_approvals VALUES (?,?,?,?,?)",
            (
                "run-1-approval",
                "approval",
                "APPROVED",
                "human",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return broker, ids, ref


def test_report_acp_traceability_recovery_and_immutability(tmp_path: Path) -> None:
    broker, ids, source = seed(tmp_path)
    worker = DocumentationWorker(broker, ids[Role.DOCUMENTATION])
    request = request_documentation(
        broker,
        ids[Role.ORCHESTRATOR],
        ids[Role.DOCUMENTATION],
        "run-1",
        "task-1",
        artifact_refs=[source],
    )
    result = worker.process_next()
    assert result is not None and result.validation_status == "VALID"
    assert result.source_refs == [source]
    report = worker.get_document(result.document_id)
    assert "F-1" in report and "unsafe behavior" in report
    assert "c" * 40 in report and "human" in report and "pytest" in report
    assert "agent claimed success" not in report
    published = broker.get_message(
        f"documentation:{request.message_id}:DOCUMENTATION_GENERATED"
    )
    assert published is not None and published.artifact_refs == [
        result.generated_artifact_ref
    ]
    assert worker.collector.artifact_intact(result.generated_artifact_ref)
    assert (
        request_documentation(
            broker,
            ids[Role.ORCHESTRATOR],
            ids[Role.DOCUMENTATION],
            "run-1",
            "task-1",
            artifact_refs=[source],
        )
        == request
    )
    assert worker.process_next() is None
    with broker.db.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM documentation_records").fetchone()[0] == 1
        )
        db.execute("UPDATE engineering_runs SET status='BLOCKED' WHERE run_id='run-1'")
    assert worker.get_document(result.document_id) == report
    doc_path = (
        tmp_path
        / "artifacts"
        / hashlib.sha256(b"run-1").hexdigest()
        / f"{result.document_id}.md"
    )
    doc_path.write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        worker.get_document(result.document_id)


def test_missing_run_fails_without_document_and_bad_scope_is_rejected(
    tmp_path: Path,
) -> None:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    identity = broker.register("documentation", Role.DOCUMENTATION)
    worker = DocumentationWorker(broker, identity)
    request = request_documentation(broker, orchestrator, identity, "missing", "task")
    assert worker.process_next() is None
    assert (
        broker.get_message(f"documentation:{request.message_id}:DOCUMENTATION_FAILED")
        is not None
    )
    with broker.db.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM documentation_records").fetchone()[0] == 0
        )


def test_adr_authorization_and_technical_proposal(tmp_path: Path) -> None:
    broker, _, ref = seed(tmp_path)
    adr = ADR(
        id="ADR-X",
        title="Title",
        context="context",
        problem="problem",
        decision="decision",
        rationale="reason",
        consequences="effects",
        status="PROPOSED",
        date=date(2026, 10, 18),
    )
    assert "PROPOSED" in render_adr(adr, broker.db)
    with pytest.raises(PermissionError):
        render_adr(
            adr.model_copy(
                update={"status": "ACCEPTED", "authorization_run_id": "run-1-approval"}
            ),
            broker.db,
        )
    with broker.db.connect() as db:
        db.execute(
            "UPDATE workflow_runs SET definition=? WHERE run_id='run-1-approval'",
            (json.dumps({"metadata": {"adr_id": "ADR-X"}}),),
        )
    assert "ACCEPTED" in render_adr(
        adr.model_copy(
            update={"status": "ACCEPTED", "authorization_run_id": "run-1-approval"}
        ),
        broker.db,
    )
    proposal = technical_documentation(
        "Module", "<script>evil</script> OPENAI_API_KEY=secret", [ref], "aec-run"
    )
    assert (
        "<script>" not in proposal
        and "secret" not in proposal
        and ref.sha256 in proposal
    )


def test_workflow_hook_and_failed_state(tmp_path: Path) -> None:
    broker, ids, _ = seed(tmp_path)
    older = request_documentation(
        broker, ids[Role.ORCHESTRATOR], ids[Role.DOCUMENTATION], "missing", "task"
    )
    with broker.db.connect() as db:
        db.execute("UPDATE engineering_runs SET status='BLOCKED' WHERE run_id='run-1'")
    worker = DocumentationWorker(broker, ids[Role.DOCUMENTATION])
    validator = workflow_documentation_validator(broker, ids[Role.ORCHESTRATOR], worker)
    assert validator({"engineering_run_id": "run-1", "task_id": "task-1"})
    assert broker.get_message(f"documentation:{older.message_id}:DOCUMENTATION_FAILED")
    result = broker.get_history("run-1")[-1]
    assert result.type == MessageType.DOCUMENTATION_GENERATED
    assert isinstance(result.payload, DocumentationResult)
    assert "BLOCKED" in worker.get_document(result.payload.document_id)


def test_missing_source_is_invalid_and_not_published(tmp_path: Path) -> None:
    broker, ids, source = seed(tmp_path)
    request = request_documentation(
        broker, ids[Role.ORCHESTRATOR], ids[Role.DOCUMENTATION], "run-1", "task-1"
    )
    path = (
        tmp_path
        / "artifacts"
        / hashlib.sha256(b"run-1").hexdigest()
        / source.artifact_id
    )
    path.unlink()
    worker = DocumentationWorker(broker, ids[Role.DOCUMENTATION])
    assert worker.process_next() is None
    assert broker.get_message(
        f"documentation:{request.message_id}:DOCUMENTATION_FAILED"
    )
    with broker.db.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM documentation_records").fetchone()[0] == 0
        )


def test_approval_without_final_qa_fails(tmp_path: Path) -> None:
    broker, ids, _ = seed(tmp_path)
    with broker.db.connect() as db:
        db.execute(
            "UPDATE engineering_iterations SET review=NULL "
            "WHERE run_id='run-1' AND number=2"
        )
    request = request_documentation(
        broker, ids[Role.ORCHESTRATOR], ids[Role.DOCUMENTATION], "run-1", "task-1"
    )
    worker = DocumentationWorker(broker, ids[Role.DOCUMENTATION])
    assert worker.process_next() is None
    assert broker.get_message(
        f"documentation:{request.message_id}:DOCUMENTATION_FAILED"
    )


def test_conflicting_hash_for_same_source_fails(tmp_path: Path) -> None:
    broker, ids, _ = seed(tmp_path)
    with broker.db.connect() as db:
        row = db.execute(
            "SELECT implementation FROM engineering_iterations "
            "WHERE run_id='run-1' AND number=2"
        ).fetchone()
        implementation = json.loads(row["implementation"])
        implementation["artifact_refs"][0]["sha256"] = "0" * 64
        db.execute(
            "UPDATE engineering_iterations SET implementation=? "
            "WHERE run_id='run-1' AND number=2",
            (json.dumps(implementation),),
        )
    request = request_documentation(
        broker, ids[Role.ORCHESTRATOR], ids[Role.DOCUMENTATION], "run-1", "task-1"
    )
    worker = DocumentationWorker(broker, ids[Role.DOCUMENTATION])
    assert worker.process_next() is None
    assert broker.get_message(
        f"documentation:{request.message_id}:DOCUMENTATION_FAILED"
    )


def test_symlink_directory_cannot_redirect_document_write(tmp_path: Path) -> None:
    broker, ids, _ = seed(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    artifact_root = tmp_path / "artifacts"
    moved = outside / "artifacts"
    artifact_root.rename(moved)
    artifact_root.symlink_to(moved, target_is_directory=True)
    request = request_documentation(
        broker, ids[Role.ORCHESTRATOR], ids[Role.DOCUMENTATION], "run-1", "task-1"
    )
    worker = DocumentationWorker(broker, ids[Role.DOCUMENTATION])
    assert worker.process_next() is None
    assert broker.get_message(
        f"documentation:{request.message_id}:DOCUMENTATION_FAILED"
    )
    with broker.db.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM documentation_records").fetchone()[0] == 0
        )


def test_redelivery_reuses_committed_document(tmp_path: Path) -> None:
    broker, ids, _ = seed(tmp_path)
    request = request_documentation(
        broker, ids[Role.ORCHESTRATOR], ids[Role.DOCUMENTATION], "run-1", "task-1"
    )
    worker = DocumentationWorker(broker, ids[Role.DOCUMENTATION])
    claim = broker.claim(ids[Role.DOCUMENTATION].agent_id, ids[Role.DOCUMENTATION])
    assert claim is not None
    first = worker._produce(claim.message)
    broker.nack(claim, "interrupted before publication", ids[Role.DOCUMENTATION])
    second = worker.process_next()
    assert second == first
    assert broker.get_message(
        f"documentation:{request.message_id}:DOCUMENTATION_GENERATED"
    )
    with broker.db.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM documentation_records").fetchone()[0] == 1
        )


def test_orphan_file_is_reconciled_without_overwrite(tmp_path: Path) -> None:
    broker, ids, _ = seed(tmp_path)
    request = request_documentation(
        broker, ids[Role.ORCHESTRATOR], ids[Role.DOCUMENTATION], "run-1", "task-1"
    )
    worker = DocumentationWorker(broker, ids[Role.DOCUMENTATION])
    document_id = hashlib.sha256(request.message_id.encode()).hexdigest()
    content = execution_report(worker.collector.collect("run-1", "task-1"), document_id)
    path = (
        tmp_path
        / "artifacts"
        / hashlib.sha256(b"run-1").hexdigest()
        / f"{document_id}.md"
    )
    path.write_text(content)
    result = worker.process_next()
    assert result is not None and result.document_id == document_id
    assert path.read_text() == content
