"""Fixture executions of published Phase 7 DAGs without model calls."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import sqlite3
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import FindingReported, MessageType
from aec.documentation.adr import ADR, record_workflow_adr
from aec.documentation.worker import DocumentationWorker, request_documentation
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.base import ExecutionAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.execution.policy import ExecutionPolicy
from aec.methodologies.definitions import (
    DECISION,
    DECISION_V101,
    SECURITY_AUDIT,
    SECURITY_AUDIT_V103,
    TDD,
    TDD_V101,
)
from aec.methodologies.static_audit import (
    StaticEvalIntegrityChecks,
    audit_snapshot,
)
from aec.methodologies.tdd import TDDChecks
from aec.methodology.contracts import MethodologyVersion
from aec.methodology.governance import MethodologyRegistry
from aec.methodology.metrics import calculate
from aec.methodology.observation import ObservationCollector
from aec.persistence.database import Database
from aec.security.identities import Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.models import WorkflowDefinition, WorkflowStatus, WorkflowTaskInput


class FixtureAdapter(ExecutionAdapter):
    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = outputs
        self.calls: list[str] = []

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        self.calls.append(request.task_id)
        if request.task_id == "design_tests":
            (request.working_directory / "test_required.py").write_text(
                "import unittest\nfrom solution import add\n"
                "class TestAdd(unittest.TestCase):\n"
                "    def test_add(self): self.assertEqual(add(2, 3), 5)\n"
            )
        elif request.task_id == "implement":
            (request.working_directory / "solution.py").write_text(
                "def add(a, b):\n    return a + b\n"
            )
            if self.outputs.get("implement") == "REMOVE_REQUIRED_TEST":
                (request.working_directory / "test_required.py").unlink()
        elif request.task_id == "refactor":
            (request.working_directory / "solution.py").write_text(
                "def add(a, b):\n    result = a + b\n    return result\n"
            )
        elif request.task_id == "blue_fix":
            if self.outputs.get("blue_fix") == "NO_IMPORT":
                (request.working_directory / "unsafe.py").write_text(
                    "def parse_literal(text):\n    return ast.literal_eval(text)\n"
                )
            elif self.outputs.get("blue_fix") != "SKIP_FIX":
                (request.working_directory / "unsafe.py").write_text(
                    "import ast\ndef parse_literal(text):\n"
                    "    return ast.literal_eval(text)\n"
                )
            if self.outputs.get("blue_fix") == "EXTRA_FILE":
                (request.working_directory / "unexpected.txt").write_text("extra\n")
        moment = datetime.now(timezone.utc)
        output = self.outputs.get(request.task_id, request.task_id)
        if request.task_id == "qa":
            output = json.dumps(
                {
                    "task_id": request.task_id,
                    "iteration": 1,
                    "reviewed_commit": "a" * 40,
                    "review_status": output,
                    "findings": [],
                }
            )
        elif request.task_id in {"red_review", "contest"}:
            output = json.dumps({"severity": "low", "description": output})
        return ExecutionResult(
            execution_id=request.execution_id,
            agent_id=request.agent_id,
            status=ExecutionStatus.SUCCEEDED,
            output=output,
            started_at=moment,
            finished_at=moment,
        )

    async def cancel(self, execution_id: str) -> bool:
        return False

    async def healthcheck(self) -> bool:
        return True


def has_direct_eval(path: Path) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "eval"
        for node in ast.walk(ast.parse(path.read_text()))
    )


def setup(
    tmp_path: Path,
    version: MethodologyVersion,
    validators: dict[str, object],
    outputs: dict[str, str] | None = None,
    *,
    workspace: Path | None = None,
    approval_bindings: dict[str, object] | None = None,
) -> tuple[WorkflowEngine, MethodologyRegistry, FixtureAdapter, DocumentationWorker]:
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    names = {step.agent for step in version.definition.steps if step.agent}
    agents = {
        name: broker.register(
            name,
            Role.QA
            if name in {"qa", "red_reviewer", "contestant"}
            else Role.IMPLEMENTER,
        )
        for name in names
    }
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    documenter = broker.register("documenter", Role.DOCUMENTATION)
    adapter = FixtureAdapter(outputs or {})
    manager = ExecutionManager(
        broker,
        {"codex": adapter},
        [tmp_path],
        policy=ExecutionPolicy((tmp_path,)),
        writable_agents=frozenset(
            name for name, agent in agents.items() if agent.role == Role.IMPLEMENTER
        ),
    )
    engine = WorkflowEngine(
        broker,
        manager,
        orchestrator,
        agents,
        gate,
        workspace or tmp_path,
        validators,  # type: ignore[arg-type]
        local_approvers=frozenset({"human"}),
        approval_bindings=approval_bindings,  # type: ignore[arg-type]
    )
    registry = MethodologyRegistry(broker.db, validators=frozenset(validators))
    registry.seed(version, actor="trusted-bootstrap")
    return engine, registry, adapter, DocumentationWorker(broker, documenter)


def test_e002_tdd_red_green_refactor_and_test_integrity(tmp_path: Path) -> None:
    checks = TDDChecks(
        tmp_path,
        (Path("test_required.py"),),
        ("python", "-m", "unittest", "test_required"),
        ExecutionPolicy((tmp_path,)),
        Database(tmp_path / "aec.sqlite"),
        "e002",
    )

    engine, registry, adapter, worker = setup(
        tmp_path,
        TDD,
        {
            "tdd_red": checks.red,
            "tdd_green": checks.green,
            "tdd_regression": checks.regression,
            "tdd_qa": checks.qa,
        },
        {"qa": "PASSED"},
    )
    run = engine.create_published(
        registry,
        TDD.methodology_id,
        TDD.version,
        "e002",
        task_input=WorkflowTaskInput(
            requirements="Add two integers",
            acceptance_criteria=("add(2, 3) returns 5",),
        ),
    )
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.WAITING_APPROVAL
    steps = engine.repository.steps(run.run_id)
    assert steps["red"].result == {"passed": False}
    assert all(
        steps[name].result == {"passed": True}
        for name in ("green", "regression", "qa_gate")
    )
    assert adapter.calls == ["design_tests", "implement", "refactor", "qa"]
    request_documentation(
        engine.broker,
        engine.orchestrator,
        worker.identity,
        run.run_id,
        run.run_id,
        documentation_type="tdd-report",
    )
    document = worker.process_next()
    assert document is not None
    report = worker.get_document(document.document_id)
    assert "tdd-adversarial@1.0.0" in report
    assert "Add two integers" in report
    assert "add(2, 3) returns 5" in report
    observation = next(
        o
        for o in ObservationCollector(engine.broker.db).collect()
        if o.run_id == run.run_id
    )
    assert observation.agent_invocations == 4
    assert observation.gates_passed is None
    pending_rate = next(
        metric
        for metric in calculate([observation], TDD.methodology_id, TDD.version)
        if metric.name == "workflow_completion_rate"
    )
    assert pending_rate.value == 0
    engine.decide(run.run_id, "approval", "human", True)
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.COMPLETED
    completed = next(
        item
        for item in ObservationCollector(engine.broker.db).collect()
        if item.run_id == run.run_id
    )
    completed_rate = next(
        metric
        for metric in calculate([completed], TDD.methodology_id, TDD.version)
        if metric.name == "workflow_completion_rate"
    )
    assert completed_rate.value == 1


@pytest.mark.parametrize(
    ("outputs", "last_call"),
    [
        ({"implement": "REMOVE_REQUIRED_TEST"}, "implement"),
        ({"qa": "INCONCLUSIVE"}, "qa"),
    ],
)
def test_tdd_blocks_removed_tests_and_unaccepted_qa(
    tmp_path: Path, outputs: dict[str, str], last_call: str
) -> None:
    run_id = f"tdd-{last_call}"
    checks = TDDChecks(
        tmp_path,
        (Path("test_required.py"),),
        ("python", "-m", "unittest", "test_required"),
        ExecutionPolicy((tmp_path,)),
        Database(tmp_path / "aec.sqlite"),
        run_id,
    )
    engine, registry, adapter, worker = setup(
        tmp_path,
        TDD,
        {
            "tdd_red": checks.red,
            "tdd_green": checks.green,
            "tdd_regression": checks.regression,
            "tdd_qa": checks.qa,
        },
        outputs,
    )
    run = engine.create_published(
        registry,
        TDD.methodology_id,
        TDD.version,
        run_id,
        task_input=WorkflowTaskInput(
            requirements="Add two integers",
            acceptance_criteria=("add(2, 3) returns 5",),
        ),
    )
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.FAILED
    assert adapter.calls[-1] == last_call


def test_e003_static_audit_stays_in_authorized_fixture(tmp_path: Path) -> None:
    (tmp_path / "fixture.py").write_text("def unsafe(data): return eval(data)\n")
    (tmp_path / "fixed_fixture.py").write_text(
        "import ast\ndef safe(data): return ast.literal_eval(data)\n"
    )
    scope = {"authorized": True}
    engine, registry, adapter, _ = setup(
        tmp_path,
        SECURITY_AUDIT,
        {
            "audit_scope": lambda _: scope["authorized"],
            "audit_static": lambda _: has_direct_eval(tmp_path / "fixture.py"),
            "audit_evidence": lambda inputs: (
                "ast.literal_eval" in inputs["proposal"]
                and not has_direct_eval(tmp_path / "fixed_fixture.py")
            ),
        },
        {
            "red_review": "eval() accepts untrusted expressions",
            "blue_proposal": "Replace eval with ast.literal_eval",
        },
    )
    run = engine.create_published(
        registry,
        SECURITY_AUDIT.methodology_id,
        SECURITY_AUDIT.version,
        "e003",
        task_input=WorkflowTaskInput(
            requirements="Review the authorized fixture for direct eval",
            acceptance_criteria=("Report static finding with evidence",),
        ),
    )
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.WAITING_APPROVAL
    assert adapter.calls == ["threat_model", "red_review", "blue_proposal"]
    finding = next(
        message
        for message in engine.broker.get_history(run.run_id)
        if message.type == MessageType.FINDING_REPORTED
    )
    assert isinstance(finding.payload, FindingReported)
    assert "eval()" in finding.payload.description and finding.artifact_refs
    assert not any(
        step.sandbox_mode == "workspace-write"
        for step in SECURITY_AUDIT.definition.steps
    )
    scope["authorized"] = False
    denied = engine.create_published(
        registry,
        SECURITY_AUDIT.methodology_id,
        SECURITY_AUDIT.version,
        "e003-denied",
        task_input=WorkflowTaskInput(
            requirements="Review the unauthorized fixture",
            acceptance_criteria=("Scope must be authorized",),
        ),
    )
    assert asyncio.run(engine.run(denied.run_id)).status == WorkflowStatus.FAILED
    assert adapter.calls == ["threat_model", "red_review", "blue_proposal"]


@pytest.mark.parametrize(
    "fix_result", ["fixed", "SKIP_FIX", "EXTRA_FILE", "NO_IMPORT"]
)
def test_static_audit_correction_and_retest_share_run(
    tmp_path: Path, fix_result: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    (project / "unsafe.py").write_text(
        "def parse_literal(text):\n    return eval(text)\n"
    )
    subprocess.run(["git", "-C", str(project), "add", "unsafe.py"], check=True)
    subprocess.run(
        [
            "git", "-C", str(project), "-c", "user.name=AEC",
            "-c", "user.email=aec@localhost", "commit", "-qm", "base",
        ],
        check=True,
    )
    checks = StaticEvalIntegrityChecks(
        project, Path("unsafe.py"), ExecutionPolicy((tmp_path,)),
        Database(tmp_path / "aec.sqlite"), "audit-correction",
    )
    engine, registry, adapter, worker = setup(
        tmp_path,
        SECURITY_AUDIT_V103,
        {
            "audit_scope": checks.scope,
            "audit_static": checks.static,
            "audit_evidence": lambda inputs: "literal_eval" in inputs["proposal"],
            "audit_retest": checks.retest,
        },
        {
            "red_review": "direct eval(text) in unsafe.py",
            "blue_proposal": "Replace with ast.literal_eval",
            "blue_fix": fix_result,
        },
        workspace=project,
        approval_bindings={"audit_artifact": checks.integrity},
    )
    run = engine.create_published(
        registry,
        SECURITY_AUDIT_V103.methodology_id,
        SECURITY_AUDIT_V103.version,
        "audit-correction",
        task_input=WorkflowTaskInput(
            requirements="Correct only unsafe.py in this authorized fixture",
            acceptance_criteria=("Remove direct eval and retest statically",),
        ),
    )
    result = asyncio.run(engine.run(run.run_id))
    valid = fix_result == "fixed"
    assert result.status == (
        WorkflowStatus.WAITING_APPROVAL if valid else WorkflowStatus.FAILED
    )
    assert adapter.calls == [
        "threat_model", "red_review", "blue_proposal", "blue_fix"
    ]
    assert checks.retest({}) is valid
    assert engine.repository.steps(run.run_id)["retest"].result == {
        "passed": valid
    }
    request_documentation(
        engine.broker,
        engine.orchestrator,
        worker.identity,
        run.run_id,
        run.run_id,
        documentation_type="security-audit-report",
    )
    document = worker.process_next()
    assert document is not None
    report = worker.get_document(document.document_id)
    assert "blue\\_fix" in report and "retest" in report
    if not valid:
        assert "Não produzido" in report


@pytest.mark.parametrize(
    "tamper_at",
    ["none", "before", "after", "restart", "restart_missing", "external", "race"],
)
def test_aec_sec_001_approval_binds_validated_audit_artifact(
    tmp_path: Path, tamper_at: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    target = project / "unsafe.py"
    vulnerable = "def parse_literal(text):\n    return eval(text)\n"
    target.write_text(vulnerable)
    subprocess.run(["git", "-C", str(project), "add", "unsafe.py"], check=True)
    subprocess.run(
        [
            "git", "-C", str(project), "-c", "user.name=AEC",
            "-c", "user.email=aec@localhost", "commit", "-qm", "base",
        ],
        check=True,
    )
    run_id = "integrity-audit"
    policy = ExecutionPolicy((tmp_path,))
    database = Database(tmp_path / "aec.sqlite")
    checks = StaticEvalIntegrityChecks(
        project, Path("unsafe.py"), policy, database, run_id
    )
    engine, registry, _, worker = setup(
        tmp_path,
        SECURITY_AUDIT_V103,
        {
            "audit_scope": checks.scope,
            "audit_static": checks.static,
            "audit_evidence": lambda inputs: "literal_eval" in inputs["proposal"],
            "audit_retest": checks.retest,
        },
        {
            "red_review": "direct eval(text) in unsafe.py",
            "blue_proposal": "Replace with ast.literal_eval",
        },
        workspace=project,
        approval_bindings={"audit_artifact": checks.integrity},
    )
    run = engine.create_published(
        registry,
        SECURITY_AUDIT_V103.methodology_id,
        SECURITY_AUDIT_V103.version,
        run_id,
        task_input=WorkflowTaskInput(
            requirements="Correct only unsafe.py",
            acceptance_criteria=("Validate exact corrected bytes",),
        ),
    )
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.WAITING_APPROVAL
    manifest, content, digest = audit_snapshot(database, run_id)
    assert manifest["version"] == "1.0.3"
    assert content == target.read_bytes()
    assert checks.integrity(run_id) == digest
    if tamper_at == "none":
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            with database.connect() as connection:
                connection.execute(
                    "UPDATE audit_artifact_snapshots SET content=? WHERE run_id=?",
                    (vulnerable.encode(), run_id),
                )
    with pytest.raises(PermissionError, match="manifest"):
        engine.decide(run_id, "approval", "human", True, manifest_sha256="0" * 64)

    if tamper_at in {"before", "external"}:
        if tamper_at == "external":
            subprocess.run(
                [
                    "python", "-c", "from pathlib import Path; "
                    "Path('unsafe.py').write_text('def parse_literal(text):\\n' "
                    "'    return eval(text)\\n')",
                ],
                cwd=project,
                check=True,
            )
        else:
            target.write_text(vulnerable)
        with pytest.raises(ValueError, match="differs"):
            engine.decide(run_id, "approval", "human", True, manifest_sha256=digest)
        assert engine.repository.approval_status(run_id, "approval") == "PENDING"
    else:
        if tamper_at in {"restart", "restart_missing"}:
            broker = Broker(tmp_path / "aec.sqlite")
            orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
            agents = {
                name: broker.register(name, Role.IMPLEMENTER)
                for name in ("threat_modeler", "blue_proposer", "blue_corrector")
            }
            agents["red_reviewer"] = broker.register("red_reviewer", Role.QA)
            gate = broker.register("quality-gate", Role.QUALITY_GATE)
            resumed_checks = StaticEvalIntegrityChecks(
                project, Path("unsafe.py"), policy, database, run_id
            )
            manager = ExecutionManager(
                broker, {"codex": FixtureAdapter({})}, [tmp_path],
                policy=policy,
            )
            engine = WorkflowEngine(
                broker, manager, orchestrator, agents, gate, project,
                {"audit_scope": resumed_checks.scope},
                local_approvers=frozenset({"human"}),
                approval_bindings=(
                    {} if tamper_at == "restart_missing"
                    else {"audit_artifact": resumed_checks.integrity}
                ),
            )
            assert engine.recover(run_id).status == WorkflowStatus.WAITING_APPROVAL
        if tamper_at == "restart_missing":
            with pytest.raises(ValueError, match="not registered"):
                engine.decide(
                    run_id, "approval", "human", True, manifest_sha256=digest
                )
            assert engine.repository.approval_status(run_id, "approval") == "PENDING"
            return
        if tamper_at == "race":
            def racing_guard(guard_run_id: str) -> str:
                result = checks.integrity(guard_run_id)
                subprocess.run(
                    [
                        "python", "-c", "from pathlib import Path; "
                        "Path('unsafe.py').write_text('def parse_literal(text):\\n' "
                        "'    return eval(text)\\n')",
                    ],
                    cwd=project,
                    check=True,
                )
                return result

            engine.approval_bindings["audit_artifact"] = racing_guard
        assert engine.decide(
            run_id, "approval", "human", True, manifest_sha256=digest
        ).status == WorkflowStatus.RUNNING
        if tamper_at == "after":
            target.write_text(vulnerable)
        final = asyncio.run(engine.run(run_id))
        assert final.status == (
            WorkflowStatus.FAILED
            if tamper_at in {"after", "race"}
            else WorkflowStatus.COMPLETED
        )
        assert engine.repository.approval_binding(run_id, "approval") == (
            digest, manifest["definition_hash"], manifest["methodology_id"],
            manifest["version"], manifest["methodology_sha256"],
        )
    request_documentation(
        worker.broker, worker.broker.register("report-orchestrator", Role.ORCHESTRATOR),
        worker.identity, run_id, run_id,
        documentation_type="security-audit-report",
    )
    document = worker.process_next()
    assert document is not None
    report = worker.get_document(document.document_id)
    assert digest in report and manifest["content_sha256"] in report


def test_preexisting_blocked_audit_run_cannot_finish(tmp_path: Path) -> None:
    engine, _, _, _ = setup(
        tmp_path, SECURITY_AUDIT_V103,
        {step.validator: lambda _: True
         for step in SECURITY_AUDIT_V103.definition.steps if step.validator},
        approval_bindings={"audit_artifact": lambda _: "0" * 64},
    )
    definition = WorkflowDefinition.model_validate({
        "id": "historical-audit", "version": "1.0.2", "name": "Historical audit",
        "steps": [{"id": "approval", "type": "human_approval"}],
    })
    run = engine.create(definition, "historical-audit")
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.WAITING_APPROVAL
    with engine.broker.db.connect() as connection:
        connection.execute(
            "INSERT INTO methodology_run_bindings VALUES (?,?,?,?)",
            (run.run_id, "security-audit", "1.0.2", "historical-hash"),
        )
    with pytest.raises(PermissionError, match="version is blocked"):
        engine.decide(run.run_id, "approval", "human", True)
    assert engine.repository.approval_status(run.run_id, "approval") == "PENDING"
    engine.repository.decide(run.run_id, "approval", "human", True)
    engine.repository.workflow_status(run.run_id, WorkflowStatus.RUNNING)
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.FAILED


@pytest.mark.parametrize(
    "evaluation,expected",
    [("inconclusive", "FAILED"), ("evidence sufficient", "WAITING_APPROVAL")],
)
def test_e004_decision_requires_evidence_and_human(
    tmp_path: Path, evaluation: str, expected: str
) -> None:
    engine, registry, adapter, worker = setup(
        tmp_path,
        DECISION,
        {
            "decision_evidence": lambda inputs: (
                inputs["evaluation"] == "evidence sufficient"
            )
        },
        {"proposal": "A or B", "contest": "A lacks evidence", "evaluation": evaluation},
    )
    run = engine.create_published(
        registry,
        DECISION.methodology_id,
        DECISION.version,
        "e004",
        task_input=WorkflowTaskInput(
            requirements="Choose between architecture A and B",
            acceptance_criteria=("Preserve evidence and uncertainty",),
        ),
    )
    assert asyncio.run(engine.run(run.run_id)).status == expected
    assert adapter.calls == ["proposal", "contest", "evaluation"]
    request_documentation(
        engine.broker,
        engine.orchestrator,
        worker.identity,
        run.run_id,
        run.run_id,
        documentation_type="decision-report",
    )
    document = worker.process_next()
    assert document is not None
    report = worker.get_document(document.document_id)
    assert "A or B" in report and "A lacks evidence" in report
    assert "ninguém" in report if expected == "WAITING_APPROVAL" else "FAILED" in report
    adr = ADR(
        id="DECISION-001",
        title="Choose A or B",
        context="Fixture architecture",
        problem="Choose one approach",
        alternatives=["A", "B"],
        decision="Pending evidence",
        rationale=evaluation,
        consequences="Review before implementation",
        references=[run.run_id],
        date=date.today(),
    )
    proposed = record_workflow_adr(adr, engine.broker, engine.orchestrator, run.run_id)
    assert proposed.status == "PROPOSED" and proposed.revision == 1
    accepted_adr = adr.model_copy(
        update={"status": "ACCEPTED", "authorization_run_id": run.run_id}
    )
    with pytest.raises(PermissionError):
        record_workflow_adr(
            accepted_adr, engine.broker, engine.orchestrator, run.run_id
        )
    if expected == "WAITING_APPROVAL":
        engine.decide(run.run_id, "approval", "human", True)
        with pytest.raises(PermissionError):
            record_workflow_adr(
                accepted_adr, engine.broker, engine.orchestrator, run.run_id
            )
        assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.COMPLETED
        accepted = record_workflow_adr(
            accepted_adr, engine.broker, engine.orchestrator, run.run_id
        )
        assert accepted.status == "ACCEPTED" and accepted.revision == 2


def test_decision_proponent_cannot_authorize_own_adr(tmp_path: Path) -> None:
    engine, registry, _, worker = setup(
        tmp_path,
        DECISION,
        {"decision_evidence": lambda _: True},
        {"evaluation": "evidence sufficient"},
    )
    run = engine.create_published(
        registry,
        DECISION.methodology_id,
        DECISION.version,
        "self-approval",
        task_input=WorkflowTaskInput(
            requirements="Choose between architecture A and B",
            acceptance_criteria=("Independent human decision",),
        ),
    )
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.WAITING_APPROVAL
    engine.local_approvers = frozenset({"proponent"})
    engine.decide(run.run_id, "approval", "proponent", True)
    assert asyncio.run(engine.run(run.run_id)).status == WorkflowStatus.COMPLETED
    adr = ADR(
        id="SELF-APPROVAL",
        title="Unsafe decision",
        context="Fixture",
        problem="Choose",
        alternatives=["A", "B"],
        decision="A",
        rationale="Claimed by proposer",
        consequences="None",
        date=date.today(),
        status="ACCEPTED",
        authorization_run_id=run.run_id,
    )
    with pytest.raises(PermissionError, match="independent"):
        record_workflow_adr(adr, engine.broker, engine.orchestrator, run.run_id)


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="opt-in four-turn Codex TDD experiment",
)
def test_real_tdd_cycle_in_trusted_fixture(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    (project / "solution.py").write_text(
        "def add(a, b):\n    raise NotImplementedError\n"
    )
    subprocess.run(
        ["git", "-C", str(project), "add", "solution.py"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=AEC",
            "-c",
            "user.email=aec@localhost",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    run_id = "real-tdd"
    policy = ExecutionPolicy((tmp_path,))
    checks = TDDChecks(
        project,
        (Path("test_required.py"),),
        ("python", "-m", "unittest", "test_required"),
        policy,
        Database(tmp_path / "aec.sqlite"),
        run_id,
        allowed_paths=("solution.py", "test_required.py"),
    )

    engine, registry, _, _ = setup(
        tmp_path,
        TDD_V101,
        {
            "tdd_red": checks.red,
            "tdd_green": checks.green,
            "tdd_regression": checks.regression,
            "tdd_qa": checks.qa,
        },
        workspace=project,
    )
    engine.manager.adapters["codex"] = CodexCliAdapter(policy=policy)
    run = engine.create_published(
        registry,
        TDD_V101.methodology_id,
        TDD_V101.version,
        run_id,
        task_input=WorkflowTaskInput(
            requirements=(
                "Implement add(a, b) in solution.py. Test Designer writes only "
                "test_required.py with a unittest for add(2, 3) == 5 and must "
                "leave solution.py unchanged so Red fails. Implementer and "
                "refactorer may edit only solution.py. Preserve the required test."
            ),
            acceptance_criteria=("add(2, 3) == 5", "required test unchanged"),
        ),
    )
    result = asyncio.run(asyncio.wait_for(engine.run(run.run_id), timeout=600))
    steps = engine.repository.steps(run.run_id)
    assert result.status == WorkflowStatus.WAITING_APPROVAL
    assert steps["red"].result == {"passed": False}
    assert all(
        steps[name].result == {"passed": True}
        for name in ("green", "regression", "qa_gate")
    )
    assert engine.repository.task_input(run.run_id) is not None


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="opt-in three-turn Codex decision experiment",
)
def test_real_decision_proposal_contest_adr(tmp_path: Path) -> None:
    def evidence(inputs: dict[str, str]) -> bool:
        output = inputs["evaluation"]
        return len(output) > 80 and "SQLite" in output and "JSON" in output

    engine, registry, _, _ = setup(
        tmp_path, DECISION_V101, {"decision_evidence": evidence}
    )
    engine.manager.adapters["codex"] = CodexCliAdapter(
        policy=ExecutionPolicy((tmp_path,))
    )
    run = engine.create_published(
        registry,
        DECISION_V101.methodology_id,
        DECISION_V101.version,
        "real-decision",
        task_input=WorkflowTaskInput(
            requirements=(
                "Choose SQLite or a single JSON file for a local append-only run "
                "log. Facts: one host, at most two writers, crash recovery needed, "
                "no remote access. Separate facts from assumptions and preserve "
                "both alternatives. Do not implement the decision."
            ),
            acceptance_criteria=(
                "Compare SQLite and JSON file using stated facts",
                "Identify uncertainty and allow an inconclusive result",
            ),
        ),
    )
    result = asyncio.run(asyncio.wait_for(engine.run(run.run_id), timeout=420))
    assert result.status == WorkflowStatus.WAITING_APPROVAL
    steps = engine.repository.steps(run.run_id)
    assert all(
        steps[name].status.value == "SUCCEEDED"
        for name in ("proposal", "contest", "evaluation", "evidence_gate")
    )
    evaluation = steps["evaluation"].result
    assert evaluation is not None
    adr = ADR(
        id="REAL-DECISION-001",
        title="Local run log storage",
        context="Trusted local fixture",
        problem="Choose SQLite or JSON file",
        alternatives=["SQLite", "JSON file"],
        decision="Pending human review",
        rationale=str(evaluation["output"]),
        consequences="No implementation before approval",
        references=[run.run_id],
        date=date.today(),
    )
    recorded = record_workflow_adr(adr, engine.broker, engine.orchestrator, run.run_id)
    assert recorded.status == "PROPOSED"


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="opt-in static audit and safe correction with real Codex",
)
def test_real_static_audit_and_safe_fixture_correction(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    vulnerable = project / "unsafe.py"
    vulnerable.write_text("def parse_literal(text):\n    return eval(text)\n")
    subprocess.run(["git", "-C", str(project), "add", "unsafe.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=AEC",
            "-c",
            "user.email=aec@localhost",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    policy = ExecutionPolicy((tmp_path,))
    database = Database(tmp_path / "aec.sqlite")
    checks = StaticEvalIntegrityChecks(
        project, Path("unsafe.py"), policy, database, "real-static-audit"
    )

    engine, registry, _, worker = setup(
        tmp_path,
        SECURITY_AUDIT_V103,
        {
            "audit_scope": checks.scope,
            "audit_static": checks.static,
            "audit_evidence": lambda inputs: "literal_eval" in inputs["proposal"],
            "audit_retest": checks.retest,
        },
        workspace=project,
        approval_bindings={"audit_artifact": checks.integrity},
    )
    engine.manager.adapters["codex"] = CodexCliAdapter(policy=policy)
    run = engine.create_published(
        registry,
        SECURITY_AUDIT_V103.methodology_id,
        SECURITY_AUDIT_V103.version,
        "real-static-audit",
        task_input=WorkflowTaskInput(
            requirements=(
                "Audit and correct only the local unsafe.py fixture. It parses "
                "Python literals with direct eval(text). Do not execute this "
                "file or external targets. Replace eval with ast.literal_eval."
            ),
            acceptance_criteria=(
                "Report the direct eval finding from static inspection",
                "Correct unsafe.py and statically retest without executing code",
            ),
        ),
    )
    result = asyncio.run(asyncio.wait_for(engine.run(run.run_id), timeout=420))
    assert result.status == WorkflowStatus.WAITING_APPROVAL
    assert any(
        message.type == MessageType.FINDING_REPORTED
        for message in engine.broker.get_history(run.run_id)
    )
    assert engine.repository.steps(run.run_id)["retest"].result == {"passed": True}
    manifest, content, digest = audit_snapshot(database, run.run_id)
    assert manifest["version"] == "1.0.3"
    assert content == vulnerable.read_bytes()
    assert checks.integrity(run.run_id) == digest
    request_documentation(
        engine.broker,
        engine.orchestrator,
        worker.identity,
        run.run_id,
        run.run_id,
        documentation_type="security-audit-report",
    )
    document = worker.process_next()
    assert document is not None
    report = worker.get_document(document.document_id)
    assert "blue\\_fix" in report and "retest" in report
    assert digest in report and manifest["content_sha256"] in report
    assert not has_direct_eval(vulnerable)
    assert "ast.literal_eval" in vulnerable.read_text()
    assert not subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
