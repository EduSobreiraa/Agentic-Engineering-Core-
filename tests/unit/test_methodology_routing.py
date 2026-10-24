import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from aec.execution.policy import ExecutionPolicy
from aec.methodologies.definitions import (
    ARENA,
    ARENA_V101,
    BUILTIN_METHODOLOGIES,
    DECISION,
    SECURITY_AUDIT,
    SECURITY_AUDIT_V102,
    TDD,
)
from aec.methodologies.router import (
    MethodologyRouter,
    RouteBlocked,
    RouteRequest,
    RuntimeReadiness,
)
from aec.methodology.governance import (
    MethodologyGovernance,
    MethodologyRegistry,
    ProposalStore,
    digest,
)
from aec.persistence.database import Database
from aec.workflows.loader import validate


def registry(tmp_path: Path) -> MethodologyRegistry:
    validators = frozenset(
        step.validator
        for version in BUILTIN_METHODOLOGIES
        for step in version.definition.steps
        if step.validator
    )
    result = MethodologyRegistry(
        Database(tmp_path / "routing.sqlite"), validators=validators
    )
    for version in BUILTIN_METHODOLOGIES:
        result.seed(version, actor="operator")
    return result


def readiness() -> RuntimeReadiness:
    return RuntimeReadiness(
        agents=frozenset({"implementer_a", "implementer_b"})
        | frozenset(
            step.agent
            for version in BUILTIN_METHODOLOGIES
            for step in version.definition.steps
            if step.agent
        ),
        executors=frozenset({"codex"}),
        validators=frozenset(
            step.validator
            for version in BUILTIN_METHODOLOGIES
            for step in version.definition.steps
            if step.validator
        ),
        max_agent_invocations=4,
    )


def test_definitions_are_valid_sequential_workflows() -> None:
    for version in BUILTIN_METHODOLOGIES:
        assert validate(version.definition)
        assert version.definition.limits.max_parallel_steps == 1
        assert version.definition.steps[-1].type == "human_approval"
        assert all(
            step.sandbox_mode == "read-only" for step in SECURITY_AUDIT.definition.steps
        )
    steps = {step.id: step for step in TDD.definition.steps}
    assert steps["implement"].condition.result_equals is False  # type: ignore[union-attr]
    assert {"red", "green", "refactor", "regression", "qa_gate"} <= set(steps)
    assert {"proposal", "contest", "evaluation", "evidence_gate"} <= {
        step.id for step in DECISION.definition.steps
    }


def test_routing_uses_published_version_and_is_reproducible(tmp_path: Path) -> None:
    router = MethodologyRouter(registry(tmp_path), readiness())
    task = RouteRequest("bug", "Fix reproducible defect", ("regression test",))
    first = router.route(task)
    assert first == router.route(task)
    assert (first.methodology_id, first.version, first.readiness) == (
        "tdd-adversarial",
        "1.0.0",
        "READY",
    )
    assert first.max_agent_invocations == 4


def test_manual_selection_requires_local_authorization(tmp_path: Path) -> None:
    router = MethodologyRouter(
        registry(tmp_path), readiness(), local_approvers=frozenset({"human"})
    )
    task = RouteRequest(
        "bug", "Choose", ("document the decision",), override="decision-adversarial"
    )
    with pytest.raises(PermissionError):
        router.route(task)
    selected = router.route(RouteRequest(**{**task.__dict__, "actor": "human"}))
    assert selected.methodology_id == "decision-adversarial"


def test_unpublished_missing_dependencies_and_resource_limits_block(
    tmp_path: Path,
) -> None:
    published = registry(tmp_path)
    router = MethodologyRouter(published, readiness())
    assert (
        router.route(
            RouteRequest("alternatives", "Compare", ("same tests",))
        ).methodology_id
        == ARENA.methodology_id
    )
    unpublished = MethodologyRegistry(
        Database(tmp_path / "unpublished.sqlite"),
        validators=frozenset({"tdd_red", "tdd_green", "tdd_regression", "tdd_qa"}),
    )
    unpublished.seed(TDD, actor="operator")
    with pytest.raises(RouteBlocked, match="not published"):
        MethodologyRouter(unpublished, readiness()).route(
            RouteRequest("alternatives", "Compare", ("same tests",))
        )
    task = RouteRequest("bug", "Fix", ("test",))
    no_gate = RuntimeReadiness(
        agents=readiness().agents,
        executors=readiness().executors,
        validators=frozenset(),
        max_agent_invocations=4,
    )
    with pytest.raises(RouteBlocked, match="dependencies unavailable"):
        MethodologyRouter(published, no_gate).route(task)
    tight = RuntimeReadiness(
        agents=readiness().agents,
        executors=readiness().executors,
        validators=readiness().validators,
        max_agent_invocations=2,
    )
    with pytest.raises(RouteBlocked, match="resource limits"):
        MethodologyRouter(published, tight).route(task)


def test_published_measured_arena_requires_declared_workload(tmp_path: Path) -> None:
    published = MethodologyRegistry(
        Database(tmp_path / "measured.sqlite"),
        validators=frozenset({"arena_selected"}),
    )
    published.seed(ARENA_V101, actor="test-fixture")
    router = MethodologyRouter(published, readiness())
    task = RouteRequest("alternatives", "Compare", ("same tests",))
    with pytest.raises(RouteBlocked, match="benchmark command"):
        router.route(task)
    measured = RouteRequest(
        **{**task.__dict__, "benchmark_argv": ("python", "benchmark.py")}
    )
    if sys.platform == "linux" and Path("/usr/bin/time").is_file():
        result = router.route(measured)
        assert (result.version, result.readiness) == ("1.0.1", "READY")
        assert "benchmark:declared" in result.dependencies
    else:
        with pytest.raises(RouteBlocked, match="unavailable"):
            router.route(measured)


def test_untrusted_security_audit_blocks(tmp_path: Path) -> None:
    router = MethodologyRouter(registry(tmp_path), readiness())
    task = RouteRequest("security_audit", "Audit fixture", ("static findings",))
    with pytest.raises(RouteBlocked, match="authorized"):
        router.route(task)
    trusted = tmp_path / "trusted"
    other = tmp_path / "other"
    trusted.mkdir()
    other.mkdir()
    router = MethodologyRouter(
        router.registry,
        readiness(),
        audit_policy=ExecutionPolicy((trusted,)),
    )
    with pytest.raises(RouteBlocked, match="not authorized"):
        router.route(RouteRequest(**{**task.__dict__, "project_path": other}))
    assert (
        router.route(
            RouteRequest(**{**task.__dict__, "project_path": trusted})
        ).methodology_id
        == "security-audit"
    )


def test_compromised_audit_version_cannot_be_routed(tmp_path: Path) -> None:
    published = MethodologyRegistry(
        Database(tmp_path / "blocked.sqlite"),
        validators=frozenset(
            step.validator for step in SECURITY_AUDIT_V102.definition.steps
            if step.validator
        ),
    )
    with pytest.raises(PermissionError, match="version is blocked"):
        published.seed(SECURITY_AUDIT_V102, actor="fixture")
    published.seed(SECURITY_AUDIT, actor="fixture")
    content = SECURITY_AUDIT_V102.model_dump_json()
    with published.db.connect() as connection:
        connection.execute(
            "INSERT INTO methodology_versions VALUES (?,?,?,?,?,?,NULL)",
            ("security-audit", "1.0.2", content, digest(content),
             datetime.now(timezone.utc).isoformat(), "historical"),
        )
        connection.execute(
            "UPDATE methodology_active SET version='1.0.2' "
            "WHERE methodology_id='security-audit'"
        )
    project = tmp_path / "trusted"
    project.mkdir()
    router = MethodologyRouter(
        published, readiness(), audit_policy=ExecutionPolicy((project,))
    )
    with pytest.raises(RouteBlocked, match="artifact integrity"):
        router.route(
            RouteRequest("security_audit", "Audit fixture", ("static finding",),
                         project_path=project)
        )


def test_compromised_audit_version_cannot_be_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "publication.sqlite")
    store = ProposalStore(database)
    monkeypatch.setattr(
        store, "get",
        lambda _: (
            SimpleNamespace(methodology_id="security-audit", proposed_version="1.0.2"),
            "APPROVED", "proposal-hash",
        ),
    )
    governance = MethodologyGovernance(
        store, MethodologyRegistry(database), local_approvers=frozenset({"human"})
    )
    with pytest.raises(PermissionError, match="version is blocked"):
        governance.publish("blocked", "human")


def test_methodology_changes_route_to_supervised_governance(tmp_path: Path) -> None:
    router = MethodologyRouter(registry(tmp_path), readiness())
    with pytest.raises(RouteBlocked, match="Phase 6"):
        router.route(RouteRequest("methodology_improvement", "Improve", ("evidence",)))
