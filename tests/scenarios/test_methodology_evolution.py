from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aec.communication.broker import Broker
from aec.communication.schemas import ArtifactRef, Message, MessageType, TaskAssigned
from aec.documentation.worker import DocumentationWorker
from aec.methodology.benchmark import LocalBenchmarkRunner
from aec.methodology.contracts import BenchmarkCase, MethodologyVersion
from aec.methodology.governance import (
    MethodologyEvaluator,
    MethodologyGovernance,
    MethodologyRegistry,
    ProposalStore,
)
from aec.methodology.metrics import calculate
from aec.methodology.observation import ObservationCollector
from aec.methodology.worker import MethodologyWorker
from aec.persistence.database import Database
from aec.security.identities import Role
from aec.workflows.models import WorkflowDefinition
from aec.workflows.repository import WorkflowRepository

CASE_A = BenchmarkCase(
    task_id="fixture-a", acceptance_sha256="a" * 64, conditions="same runner"
)
CASE_B = BenchmarkCase(
    task_id="fixture-b", acceptance_sha256="b" * 64, conditions="same runner"
)


def fixture_benchmark(definition: WorkflowDefinition, case: BenchmarkCase) -> float:
    values = {"fixture-a": (3.0, 2.0), "fixture-b": (2.0, 1.0)}
    return values[case.task_id][definition.version != "1.0.0"]


def failing_benchmark(definition: WorkflowDefinition, case: BenchmarkCase) -> float:
    raise RuntimeError("runner stopped")


def base_version() -> MethodologyVersion:
    return MethodologyVersion(
        methodology_id="dev-adversarial",
        version="1.0.0",
        definition=WorkflowDefinition.model_validate(
            {
                "id": "dev-adversarial",
                "version": "1.0.0",
                "name": "Adversarial",
                "steps": [
                    {
                        "id": "implement",
                        "type": "agent",
                        "agent": "implementer",
                        "executor": "fake",
                        "inputs": {"prompt": "implement"},
                    },
                    {
                        "id": "qa",
                        "type": "agent",
                        "agent": "qa",
                        "executor": "fake",
                        "depends_on": ["implement"],
                        "inputs": {"prompt": "review", "artifact_id": "artifact"},
                    },
                    {"id": "approval", "type": "human_approval", "depends_on": ["qa"]},
                ],
            }
        ),
        mandatory_gates=frozenset(),
    )


def history(db: Database, run_id: str) -> None:
    import json

    review = {
        "task_id": run_id,
        "iteration": 1,
        "reviewed_commit": "a" * 40,
        "review_status": "FAILED",
        "findings": [
            {
                "finding_id": run_id,
                "severity": "HIGH",
                "category": "typing",
                "title": "Type error",
                "description": "Wrong type",
                "status": "REPRODUCED",
                "reproduction_steps": ["run type checker"],
            }
        ],
    }
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO engineering_runs VALUES "
            "(?,?,?,?,?,?,?,?,?,1,3,100,'[]','APPROVED',NULL,?)",
            (
                run_id,
                run_id,
                "task",
                "[]",
                "[]",
                "/tmp",
                "/tmp",
                "a" * 40,
                "a" * 40,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO engineering_iterations(run_id,number,status,review,gates) "
            "VALUES (?,1,'GATED',?,?)",
            (
                run_id,
                json.dumps(review),
                json.dumps(
                    [
                        {
                            "gate_id": "unit",
                            "validator": "pytest",
                            "exit_code": 0,
                            "status": "PASSED",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "evidence": "fixture",
                        }
                    ]
                ),
            ),
        )
        connection.execute(
            "INSERT INTO engineering_iterations(run_id,number,status,review,gates) "
            "VALUES (?,2,'GATED',?,?)",
            (
                run_id,
                json.dumps(
                    {
                        "task_id": run_id,
                        "iteration": 2,
                        "reviewed_commit": "a" * 40,
                        "review_status": "PASSED",
                        "findings": [],
                    }
                ),
                json.dumps(
                    [
                        {
                            "gate_id": "unit",
                            "validator": "pytest",
                            "exit_code": 0,
                            "status": "PASSED",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "evidence": "fixture",
                        }
                    ]
                ),
            ),
        )


def test_supervised_evolution(tmp_path: object) -> None:
    from pathlib import Path

    directory = Path(str(tmp_path))
    db = Database(directory / "aec.sqlite")
    registry = MethodologyRegistry(db, validators=frozenset({"type_check"}))
    registry.seed(base_version(), actor="bootstrap")
    assert registry.list_methodologies() == ["dev-adversarial"]
    history(db, "run-021")
    history(db, "run-025")
    store = ProposalStore(db)
    worker = MethodologyWorker(ObservationCollector(db), store)
    finding, proposal = worker.propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-001",
        proposed_version="1.1.0",
        expected_metric="acceptance_rate",
    )
    assert finding.evidence_refs == ("run-021", "run-025")
    broker = Broker(directory / "aec.sqlite")
    method_identity = broker.register("method", Role.METHODOLOGY)
    documentation_identity = broker.register("documentation", Role.DOCUMENTATION)
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    broker.publish(
        Message(
            protocol="ACP",
            version="0.1",
            message_id="historical-assignment",
            run_id="run-021",
            task_id="run-021",
            sender=orchestrator.agent_id,
            recipient=implementer.agent_id,
            type=MessageType.TASK_ASSIGNED,
            iteration=0,
            created_at=datetime.now(timezone.utc),
            payload=TaskAssigned(title="Fixture", instructions="Implement"),
        ),
        orchestrator,
    )
    assert "historical-assignment" in ObservationCollector(db).collect()[0].message_ids
    worker.notify(proposal, broker, method_identity, orchestrator)
    worker.notify(proposal, broker, method_identity, orchestrator)
    assert len(broker.get_history(proposal.proposal_id)) == 1
    assert broker.get_history(proposal.proposal_id)[0].metadata["proposal_sha256"]
    workflow_repository = WorkflowRepository(db)
    workflow_repository.create(
        "old-workflow", registry.active("dev-adversarial").definition
    )
    registry.bind_run("old-workflow", "dev-adversarial", "1.0.0")
    metrics = calculate(ObservationCollector(db).collect(), "dev-adversarial", "1.0.0")
    assert {item.name: item.value for item in metrics}["validation_success_rate"] == 1
    assert {item.name: item.value for item in metrics}[
        "documentation_completeness"
    ] is None
    documentation = DocumentationWorker(broker, documentation_identity)
    rfc_id = documentation.create_methodology_document(proposal.proposal_id, "rfc")
    assert "run-021" in documentation.get_document(rfc_id)
    assert (
        documentation.create_methodology_document(proposal.proposal_id, "rfc") == rfc_id
    )
    governance = MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human-test"})
    )
    with pytest.raises(PermissionError):
        governance.publish(proposal.proposal_id, "human-test")
    _, regression = MethodologyWorker(
        ObservationCollector(db), store
    ).propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-002",
        proposed_version="1.2.0",
    )
    failed = MethodologyEvaluator(store, registry).evaluate(
        regression.proposal_id,
        cases=(CASE_A,),
        benchmark=lambda definition, case: (
            2.0 if definition.version == "1.0.0" else 3.0
        ),
    )
    assert failed.verdict == "FAIL"
    assert store.get(regression.proposal_id)[1] == "EVALUATION_FAILED"
    _, interrupted = MethodologyWorker(
        ObservationCollector(db), store
    ).propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-003",
        proposed_version="1.3.0",
    )
    assert (
        MethodologyEvaluator(store, registry)
        .evaluate(interrupted.proposal_id, cases=(CASE_A,), benchmark=failing_benchmark)
        .verdict
        == "FAIL"
    )
    evaluation = asyncio.run(
        MethodologyEvaluator(store, registry).evaluate_controlled(
            proposal.proposal_id, LocalBenchmarkRunner(directory / "benchmarks")
        )
    )
    assert evaluation.verdict == "PASS_WITH_WARNINGS"
    assert (evaluation.baseline, evaluation.candidate) == (0.5, 1.0)
    assert [pair.task_id for pair in evaluation.pairs] == [
        "valid-types-1",
        "invalid-types-1",
    ]
    evaluation_id = documentation.create_methodology_document(
        proposal.proposal_id, "evaluation"
    )
    assert "PASS\\_WITH\\_WARNINGS" in documentation.get_document(evaluation_id)
    with pytest.raises(PermissionError):
        governance.decide(proposal.proposal_id, "methodology-worker", "APPROVED")
    approval = governance.decide(proposal.proposal_id, "human-test", "APPROVED")
    assert approval.actor == "human-test"
    decision_id = documentation.create_methodology_document(
        proposal.proposal_id, "decision"
    )
    assert "human-test" in documentation.get_document(decision_id)
    publication = governance.publish(proposal.proposal_id, "human-test")
    publication_id = documentation.create_methodology_document(
        proposal.proposal_id, "publication"
    )
    assert publication.sha256 in documentation.get_document(publication_id)
    assert governance.publish(proposal.proposal_id, "human-test") == publication
    assert registry.active("dev-adversarial").version == "1.1.0"
    assert registry.get("dev-adversarial", "1.0.0").version == "1.0.0"
    assert registry.versions("dev-adversarial") == ["1.0.0", "1.1.0"]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with db.connect() as connection:
            connection.execute(
                "UPDATE methodology_versions SET sha256=? "
                "WHERE methodology_id=? AND version=?",
                ("0" * 64, "dev-adversarial", "1.0.0"),
            )
    workflow_repository.create(
        "new-workflow", registry.active("dev-adversarial").definition
    )
    registry.bind_run("new-workflow", "dev-adversarial", "1.1.0")
    with db.connect() as connection:
        bindings = connection.execute(
            "SELECT run_id,version FROM methodology_run_bindings ORDER BY run_id"
        ).fetchall()
    assert [(row["run_id"], row["version"]) for row in bindings] == [
        ("new-workflow", "1.1.0"),
        ("old-workflow", "1.0.0"),
    ]
    assert [event["kind"] for event in store.history(proposal.proposal_id)] == [
        "DRAFT",
        "SUBMITTED",
        "UNDER_EVALUATION",
        "PASS_WITH_WARNINGS",
        "APPROVED",
        "PUBLISHED",
    ]


def test_documentation_manifest_checks_persisted_artifact(tmp_path: object) -> None:
    from pathlib import Path

    directory = Path(str(tmp_path))
    db = Database(directory / "aec.sqlite")
    version = base_version().model_copy(
        update={"required_documents": frozenset({"execution-report"})}
    )
    MethodologyRegistry(db).seed(version, actor="bootstrap")
    history(db, "run-021")
    broker = Broker(directory / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    broker.publish(
        Message(
            protocol="ACP",
            version="0.1",
            message_id="documentation-source",
            run_id="run-021",
            task_id="run-021",
            sender=orchestrator.agent_id,
            recipient=implementer.agent_id,
            type=MessageType.TASK_ASSIGNED,
            iteration=0,
            created_at=datetime.now(timezone.utc),
            payload=TaskAssigned(title="Fixture", instructions="Implement"),
        ),
        orchestrator,
    )
    metrics = calculate(ObservationCollector(db).collect(), "dev-adversarial", "1.0.0")
    assert {item.name: item.value for item in metrics}[
        "documentation_completeness"
    ] == 0
    content = "# Report\n"
    artifact_id = "report.md"
    artifact_dir = directory / "artifacts" / hashlib.sha256(b"run-021").hexdigest()
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / artifact_id
    artifact.write_text(content)
    ref = ArtifactRef(
        artifact_id=artifact_id,
        kind="documentation/markdown",
        uri=f"artifact://run-021/{artifact_id}",
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        created_by="documentation",
    )
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO documentation_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "doc-021",
                "documentation-source",
                "run-021",
                "run-021",
                "execution-report",
                content,
                ref.sha256,
                ref.model_dump_json(),
                "[]",
                "VALID",
                "[]",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    metrics = calculate(ObservationCollector(db).collect(), "dev-adversarial", "1.0.0")
    assert {item.name: item.value for item in metrics}[
        "documentation_completeness"
    ] == 1
    artifact.write_text("tampered")
    metrics = calculate(ObservationCollector(db).collect(), "dev-adversarial", "1.0.0")
    assert {item.name: item.value for item in metrics}[
        "documentation_completeness"
    ] == 0


def test_inconclusive_and_duplicate(tmp_path: object) -> None:
    from pathlib import Path

    db = Database(Path(str(tmp_path)) / "aec.sqlite")
    registry = MethodologyRegistry(db, validators=frozenset({"type_check"}))
    registry.seed(base_version(), actor="bootstrap")
    history(db, "run-021")
    history(db, "run-025")
    store = ProposalStore(db)
    _, proposal = MethodologyWorker(ObservationCollector(db), store).propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-001",
        proposed_version="1.1.0",
    )
    with pytest.raises(Exception):
        store.submit(proposal)
    with pytest.raises(ValueError, match="unknown evidence"):
        store.submit(
            proposal.model_copy(
                update={
                    "proposal_id": "RFC-METHOD-BAD",
                    "evidence_refs": ("missing-run",),
                }
            )
        )
    with pytest.raises(ValueError, match="base version"):
        store.submit(
            proposal.model_copy(
                update={
                    "proposal_id": "RFC-METHOD-UNKNOWN",
                    "base_version": "9.0.0",
                }
            )
        )
    result = MethodologyEvaluator(store, registry).evaluate(proposal.proposal_id)
    assert result.verdict == "INCONCLUSIVE"
    governance = MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human-test"})
    )
    with pytest.raises(ValueError):
        governance.decide(proposal.proposal_id, "human-test", "APPROVED")
    governance.decide(proposal.proposal_id, "human-test", "REJECTED")
    with pytest.raises(PermissionError):
        governance.publish(proposal.proposal_id, "human-test")
    _, revision = MethodologyWorker(ObservationCollector(db), store).propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-REVISE",
        proposed_version="1.2.0",
    )
    MethodologyEvaluator(store, registry).evaluate(
        revision.proposal_id,
        cases=(CASE_A,),
        benchmark=fixture_benchmark,
    )
    with pytest.raises(ValueError, match="executed evaluation"):
        governance.decide(revision.proposal_id, "human-test", "APPROVED")
    governance.decide(revision.proposal_id, "human-test", "CHANGES_REQUESTED")
    with pytest.raises(ValueError, match="not awaiting approval"):
        governance.decide(revision.proposal_id, "human-test", "APPROVED")


def test_publication_failure_can_retry(tmp_path: object) -> None:
    from pathlib import Path

    directory = Path(str(tmp_path))
    db = Database(directory / "aec.sqlite")
    registry = MethodologyRegistry(db)
    registry.seed(base_version(), actor="bootstrap")
    history(db, "run-021")
    history(db, "run-025")
    store = ProposalStore(db)
    _, proposal = MethodologyWorker(ObservationCollector(db), store).propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-001",
        proposed_version="1.1.0",
        expected_metric="acceptance_rate",
    )
    asyncio.run(
        MethodologyEvaluator(store, registry).evaluate_controlled(
            proposal.proposal_id, LocalBenchmarkRunner(directory / "benchmarks")
        )
    )
    governance = MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human-test"})
    )
    governance.decide(proposal.proposal_id, "human-test", "APPROVED")
    with pytest.raises(ValueError, match="unregistered validators"):
        governance.publish(proposal.proposal_id, "human-test")
    assert store.get(proposal.proposal_id)[1] == "APPROVED"
    assert registry.active("dev-adversarial").version == "1.0.0"
    configured = MethodologyRegistry(db, validators=frozenset({"type_check"}))
    MethodologyGovernance(
        store, configured, local_approvers=frozenset({"human-test"})
    ).publish(proposal.proposal_id, "human-test")
    assert configured.active("dev-adversarial").version == "1.1.0"
    _, stale = MethodologyWorker(ObservationCollector(db), store).propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-002",
        proposed_version="1.2.0",
        expected_metric="acceptance_rate",
    )
    asyncio.run(
        MethodologyEvaluator(store, configured).evaluate_controlled(
            stale.proposal_id, LocalBenchmarkRunner(directory / "benchmarks")
        )
    )
    second = MethodologyGovernance(
        store, configured, local_approvers=frozenset({"human-test"})
    )
    second.decide(stale.proposal_id, "human-test", "APPROVED")
    with pytest.raises(ValueError, match="base version conflict"):
        second.publish(stale.proposal_id, "human-test")
    assert store.get(stale.proposal_id)[1] == "APPROVED"
    with db.connect() as connection:
        connection.execute(
            "UPDATE methodology_proposals SET content=? WHERE proposal_id=?",
            ("{}", stale.proposal_id),
        )
    with pytest.raises(RuntimeError, match="hash mismatch"):
        second.publish(stale.proposal_id, "human-test")


def test_inconsistent_gate_record_fails_closed(tmp_path: object) -> None:
    from pathlib import Path

    db = Database(Path(str(tmp_path)) / "aec.sqlite")
    history(db, "run-corrupt")
    with db.connect() as connection:
        connection.execute(
            "UPDATE engineering_iterations SET gates=? WHERE run_id=?",
            ('[{"passed": true}]', "run-corrupt"),
        )
    with pytest.raises(ValidationError):
        ObservationCollector(db).collect()


def test_evaluation_rejects_missing_qa(tmp_path: object) -> None:
    from pathlib import Path

    db = Database(Path(str(tmp_path)) / "aec.sqlite")
    invalid_base = MethodologyVersion(
        methodology_id="dev-adversarial",
        version="1.0.0",
        definition=WorkflowDefinition.model_validate(
            {
                "id": "dev-adversarial",
                "version": "1.0.0",
                "name": "No QA",
                "steps": [
                    {
                        "id": "implement",
                        "type": "agent",
                        "agent": "implementer",
                        "executor": "fake",
                        "inputs": {"prompt": "implement"},
                    }
                ],
            }
        ),
        mandatory_gates=frozenset(),
    )
    registry = MethodologyRegistry(db, validators=frozenset({"type_check"}))
    registry.seed(invalid_base, actor="bootstrap")
    history(db, "run-021")
    history(db, "run-025")
    store = ProposalStore(db)
    _, proposal = MethodologyWorker(ObservationCollector(db), store).propose_type_gate(
        "dev-adversarial",
        "1.0.0",
        proposal_id="RFC-METHOD-001",
        proposed_version="1.1.0",
    )
    result = MethodologyEvaluator(store, registry).evaluate(proposal.proposal_id)
    assert result.verdict == "FAIL"
    assert "QA step" in result.checks[0]
