"""Governed publication of an exact complete methodology revision."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Literal

import pytest

from aec.communication.broker import Broker
from aec.documentation.worker import DocumentationWorker
from aec.methodologies.definitions import SECURITY_AUDIT, SECURITY_AUDIT_V103
from aec.methodology.contracts import (
    MethodologyProposal,
    RevisionCheck,
    RevisionVerification,
    methodology_digest,
)
from aec.methodology.governance import (
    MethodologyEvaluator,
    MethodologyGovernance,
    MethodologyRegistry,
    ProposalStore,
    digest,
    revision_evidence_digest,
)
from aec.persistence.database import Database
from aec.security.identities import Role
from aec.workflows.loader import definition_hash
from aec.workflows.repository import WorkflowRepository

AUDIT_VALIDATORS = frozenset(
    step.validator
    for step in SECURITY_AUDIT_V103.definition.steps
    if step.validator is not None
)


def prepared(tmp_path: Path) -> tuple[
    Database, MethodologyRegistry, ProposalStore, MethodologyProposal
]:
    db = Database(tmp_path / "aec.sqlite")
    registry = MethodologyRegistry(db, validators=AUDIT_VALIDATORS)
    registry.seed(SECURITY_AUDIT, actor="bootstrap")
    WorkflowRepository(db).create("audit-evidence", SECURITY_AUDIT_V103.definition)
    with db.connect() as connection:
        connection.execute(
            "UPDATE workflow_runs SET status='WAITING_APPROVAL' "
            "WHERE run_id='audit-evidence'"
        )
        connection.execute(
            "UPDATE workflow_steps SET status='SUCCEEDED' WHERE run_id=? "
            "AND step_id IN ('authorize_scope','static_analysis',"
            "'verify_evidence','retest')",
            ("audit-evidence",),
        )
    proposal = MethodologyProposal(
        kind="METHODOLOGY_REVISION",
        proposal_id="AEC-GOV-003-AUDIT",
        methodology_id="security-audit",
        base_version="1.0.0",
        proposed_version="1.0.3",
        problem="Static audit needs a bound correction and retest",
        hypothesis="The revised workflow preserves exact validated bytes",
        evidence_refs=("audit-evidence",),
        revision=SECURITY_AUDIT_V103,
        candidate_sha256=methodology_digest(SECURITY_AUDIT_V103),
        expected_metric="mandatory_gate_pass_rate",
        expected_direction="increase",
        risks="Workspace writes require trusted local scope",
        validation_criteria="All mandatory gates and artifact binding pass",
        rollback_plan="Keep 1.0.0 active for new runs",
    )
    store = ProposalStore(db)
    store.submit(proposal)
    return db, registry, store, proposal


def verification(candidate_sha256: str) -> RevisionVerification:
    return RevisionVerification(
        candidate_sha256=candidate_sha256,
        evidence_refs=("audit-evidence",),
        checks=(
            RevisionCheck(
                name="artifact-integrity", passed=True,
                evidence="audit-evidence: committed bytes and digest verified",
            ),
        ),
        limitations=("Trusted local fixture only.",),
    )


def test_revision_evidence_binds_arena_and_tdd_records(tmp_path: Path) -> None:
    db, _, _, _ = prepared(tmp_path)
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO arena_comparisons VALUES (?,?,?)",
            ("audit-evidence", "comparison-a", digest("comparison-a")),
        )
        connection.execute(
            "INSERT INTO tdd_red_snapshots VALUES (?,?,?)",
            ("audit-evidence", "red-a", digest("red-a")),
        )
        before = revision_evidence_digest(connection, ("audit-evidence",))
        connection.execute("DROP TRIGGER arena_comparisons_no_update")
        connection.execute(
            "UPDATE arena_comparisons SET content=?,sha256=? WHERE run_id=?",
            ("comparison-b", digest("comparison-b"), "audit-evidence"),
        )
        assert revision_evidence_digest(connection, ("audit-evidence",)) != before
        connection.execute("DROP TRIGGER tdd_red_snapshots_no_update")
        connection.execute(
            "UPDATE tdd_red_snapshots SET content=? WHERE run_id=?",
            ("red-b", "audit-evidence"),
        )
        with pytest.raises(ValueError, match="tdd_red_snapshots evidence"):
            revision_evidence_digest(connection, ("audit-evidence",))


def test_full_revision_requires_exact_human_digest_and_publishes_atomically(
    tmp_path: Path,
) -> None:
    db, registry, store, proposal = prepared(tmp_path)
    result = MethodologyEvaluator(store, registry).evaluate_revision(
        proposal.proposal_id,
        lambda candidate: verification(methodology_digest(candidate)),
    )
    assert result.verdict == "PASS_WITH_WARNINGS"
    assert result.candidate_sha256 == proposal.candidate_sha256
    assert {change.category for change in result.semantic_diff} >= {
        "agent", "gate", "permission", "transition", "state"
    }
    governance = MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human"})
    )
    with pytest.raises(PermissionError):
        governance.decide(
            proposal.proposal_id, "agent", "APPROVED",
            candidate_sha256=proposal.candidate_sha256,
        )
    with pytest.raises(ValueError, match="evaluated candidate"):
        governance.decide(proposal.proposal_id, "human", "APPROVED")
    with pytest.raises(ValueError, match="evaluated candidate"):
        governance.decide(
            proposal.proposal_id, "human", "APPROVED",
            candidate_sha256="0" * 64,
        )
    assert registry.active("security-audit").version == "1.0.0"
    approval = governance.decide(
        proposal.proposal_id, "human", "APPROVED",
        candidate_sha256=proposal.candidate_sha256,
    )
    assert approval.candidate_sha256 == proposal.candidate_sha256
    with pytest.raises(PermissionError):
        governance.publish(proposal.proposal_id, "agent")
    published = governance.publish(proposal.proposal_id, "human")
    assert published.version == "1.0.3"
    assert governance.publish(proposal.proposal_id, "human") == published
    assert registry.active("security-audit").definition == (
        SECURITY_AUDIT_V103.definition
    )
    broker = Broker(db.path)
    documentation = DocumentationWorker(
        broker, broker.register("documentation", Role.DOCUMENTATION)
    )
    documents: tuple[
        tuple[Literal["rfc", "evaluation", "decision", "publication"], str], ...
    ] = (
        ("rfc", "Digest da candidata"),
        ("evaluation", "Digest das evidências"),
        ("decision", "Digest aprovado"),
        ("publication", "Hash da versão"),
    )
    for kind, expected in documents:
        document_id = documentation.create_methodology_document(
            proposal.proposal_id, kind
        )
        assert expected in documentation.get_document(document_id)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT definition,sha256,approved_by FROM methodology_versions "
            "WHERE methodology_id='security-audit' AND version='1.0.3'"
        ).fetchone()
        assert digest(row["definition"]) == row["sha256"]
        assert row["approved_by"] == "human"
        assert connection.execute(
            "SELECT approval_candidate_sha256 FROM methodology_proposals "
            "WHERE proposal_id=?", (proposal.proposal_id,)
        ).fetchone()[0] == proposal.candidate_sha256
    with pytest.raises(PermissionError, match="blocked"):
        registry.seed(
            SECURITY_AUDIT_V103.model_copy(update={"version": "1.0.2"}),
            actor="agent",
        )


@pytest.mark.parametrize("when", ["before_approval", "after_approval"])
def test_changed_candidate_cannot_be_approved_or_published(
    tmp_path: Path, when: str
) -> None:
    db, registry, store, proposal = prepared(tmp_path)
    MethodologyEvaluator(store, registry).evaluate_revision(
        proposal.proposal_id,
        lambda candidate: verification(methodology_digest(candidate)),
    )
    governance = MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human"})
    )
    if when == "after_approval":
        governance.decide(
            proposal.proposal_id, "human", "APPROVED",
            candidate_sha256=proposal.candidate_sha256,
        )
    assert proposal.revision is not None
    altered = proposal.revision.model_copy(deep=True)
    altered.definition.steps[1].inputs["prompt"] = "Changed after evaluation"
    altered_proposal = MethodologyProposal.model_validate({
        **proposal.model_dump(), "revision": altered,
        "candidate_sha256": methodology_digest(altered),
    })
    content = altered_proposal.model_dump_json()
    with db.connect() as connection:
        connection.execute(
            "UPDATE methodology_proposals SET content=?,sha256=? WHERE proposal_id=?",
            (content, digest(content), proposal.proposal_id),
        )
    if when == "before_approval":
        with pytest.raises(ValueError, match="evaluated candidate"):
            governance.decide(
                proposal.proposal_id, "human", "APPROVED",
                candidate_sha256=altered_proposal.candidate_sha256,
            )
    else:
        with pytest.raises(PermissionError, match="evaluation does not permit"):
            governance.publish(proposal.proposal_id, "human")
    assert registry.active("security-audit").version == "1.0.0"


@pytest.mark.parametrize("when", ["before_approval", "after_approval"])
def test_changed_evidence_cannot_be_approved_or_published(
    tmp_path: Path, when: str
) -> None:
    db, registry, store, proposal = prepared(tmp_path)
    result = MethodologyEvaluator(store, registry).evaluate_revision(
        proposal.proposal_id,
        lambda candidate: verification(methodology_digest(candidate)),
    )
    assert result.evidence_sha256 is not None
    governance = MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human"})
    )
    if when == "after_approval":
        governance.decide(
            proposal.proposal_id, "human", "APPROVED",
            candidate_sha256=proposal.candidate_sha256,
        )
    with db.connect() as connection:
        connection.execute(
            "UPDATE workflow_steps SET result=? WHERE run_id=? AND step_id=?",
            ('{"changed":true}', "audit-evidence", "retest"),
        )
    if when == "before_approval":
        with pytest.raises(ValueError, match="evidence changed"):
            governance.decide(
                proposal.proposal_id, "human", "APPROVED",
                candidate_sha256=proposal.candidate_sha256,
            )
    else:
        with pytest.raises(PermissionError, match="approved candidate changed"):
            governance.publish(proposal.proposal_id, "human")
    assert registry.active("security-audit").version == "1.0.0"


def test_human_can_reject_revision_after_evidence_changes(tmp_path: Path) -> None:
    db, registry, store, proposal = prepared(tmp_path)
    MethodologyEvaluator(store, registry).evaluate_revision(
        proposal.proposal_id,
        lambda candidate: verification(methodology_digest(candidate)),
    )
    with db.connect() as connection:
        connection.execute(
            "UPDATE workflow_steps SET result=? WHERE run_id=? AND step_id=?",
            ('{"changed":true}', "audit-evidence", "retest"),
        )
    governance = MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human"})
    )
    assert governance.decide(
        proposal.proposal_id, "human", "REJECTED",
        candidate_sha256=proposal.candidate_sha256,
    ).decision == "REJECTED"
    assert registry.active("security-audit").version == "1.0.0"


def test_revision_verifier_and_evidence_must_match_exact_definition(
    tmp_path: Path,
) -> None:
    db, registry, store, proposal = prepared(tmp_path)
    result = MethodologyEvaluator(store, registry).evaluate_revision(
        proposal.proposal_id,
        lambda _: verification("0" * 64),
    )
    assert result.verdict == "FAIL"
    with pytest.raises(ValueError, match="not awaiting approval"):
        MethodologyGovernance(
            store, registry, local_approvers=frozenset({"human"})
        ).decide(
            proposal.proposal_id, "human", "APPROVED",
            candidate_sha256=proposal.candidate_sha256,
        )
    assert registry.active("security-audit").version == "1.0.0"
    with db.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM methodology_versions "
            "WHERE methodology_id='security-audit' AND version='1.0.2'"
        ).fetchone()[0] == 0


def test_revision_rejects_missing_mandatory_gate(tmp_path: Path) -> None:
    _, registry, store, proposal = prepared(tmp_path)
    altered = SECURITY_AUDIT_V103.model_copy(
        update={"mandatory_gates": frozenset({"audit_retest"})}
    )
    invalid = MethodologyProposal.model_validate({
        **proposal.model_dump(), "revision": altered,
        "candidate_sha256": methodology_digest(altered),
        "proposal_id": "AEC-GOV-003-INVALID",
    })
    store.submit(invalid)
    result = MethodologyEvaluator(store, registry).evaluate_revision(
        invalid.proposal_id,
        lambda candidate: verification(methodology_digest(candidate)),
    )
    assert result.verdict == "FAIL"
    assert "removes a mandatory gate" in result.checks[0]


def test_canonical_digest_and_diff_survive_serialization() -> None:
    content = SECURITY_AUDIT_V103.model_dump(mode="json")
    content["mandatory_gates"] = list(reversed(content["mandatory_gates"]))
    restored = type(SECURITY_AUDIT_V103).model_validate_json(json.dumps(content))
    assert methodology_digest(restored) == methodology_digest(SECURITY_AUDIT_V103)
    assert definition_hash(restored.definition) == definition_hash(
        SECURITY_AUDIT_V103.definition
    )


def test_registered_external_run_stays_bound_to_source_and_artifact(
    tmp_path: Path,
) -> None:
    db, registry, store, original = prepared(tmp_path)
    source = Database(tmp_path / "source.sqlite")
    source_registry = MethodologyRegistry(source, validators=AUDIT_VALIDATORS)
    source_registry.seed(SECURITY_AUDIT_V103, actor="fixture-bootstrap")
    WorkflowRepository(source).create("source-audit", SECURITY_AUDIT_V103.definition)
    source_registry.bind_run("source-audit", "security-audit", "1.0.3")
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    target = project / "unsafe.py"
    target.write_text("def parse_literal(text):\n    return eval(text)\n")

    def commit(message: str) -> str:
        subprocess.run(["git", "-C", str(project), "add", "unsafe.py"], check=True)
        subprocess.run([
            "git", "-C", str(project), "-c", "user.name=AEC",
            "-c", "user.email=aec@localhost", "commit", "-qm", message,
        ], check=True)
        return subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    base = commit("vulnerable")
    target.write_text(
        "import ast\ndef parse_literal(text):\n    return ast.literal_eval(text)\n"
    )
    corrected = commit("corrected")
    with source.connect() as connection:
        published = connection.execute(
            "SELECT sha256 FROM methodology_versions "
            "WHERE methodology_id='security-audit' AND version='1.0.3'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE workflow_runs SET status='WAITING_APPROVAL' "
            "WHERE run_id='source-audit'"
        )
        connection.execute(
            "UPDATE workflow_steps SET status='SUCCEEDED' WHERE run_id=? "
            "AND step_id IN ('authorize_scope','static_analysis',"
            "'verify_evidence','retest')", ("source-audit",),
        )
        manifest = json.dumps({
            "run_id": "source-audit", "methodology_id": "security-audit",
            "version": "1.0.3", "methodology_sha256": published,
            "definition_hash": definition_hash(SECURITY_AUDIT_V103.definition),
            "base_commit": base, "commit": corrected, "path": "unsafe.py",
            "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "INSERT INTO audit_artifact_snapshots VALUES (?,?,?,?)",
            ("source-audit", manifest, digest(manifest), target.read_bytes()),
        )
    evidence_id = "external-audit"
    store.register_external_evidence(
        evidence_id, Path(source.path), "source-audit", project,
        SECURITY_AUDIT_V103,
    )
    proposal = MethodologyProposal.model_validate({
        **original.model_dump(),
        "proposal_id": "EXTERNAL-AUDIT", "evidence_refs": (evidence_id,),
    })
    store.submit(proposal)
    result = MethodologyEvaluator(store, registry).evaluate_revision(
        proposal.proposal_id,
        lambda candidate: RevisionVerification(
            candidate_sha256=methodology_digest(candidate),
            evidence_refs=(evidence_id,),
            checks=(RevisionCheck(
                name="source-check", passed=True,
                evidence="source run, mandatory gates, snapshot and Git blob",
            ),),
        ),
    )
    assert result.verdict == "PASS"
    target.write_text("def parse_literal(text):\n    return eval(text)\n")
    with pytest.raises(ValueError, match="artifact differs"):
        MethodologyGovernance(
            store, registry, local_approvers=frozenset({"human"})
        ).decide(
            proposal.proposal_id, "human", "APPROVED",
            candidate_sha256=proposal.candidate_sha256,
        )
    assert registry.active("security-audit").version == "1.0.0"
