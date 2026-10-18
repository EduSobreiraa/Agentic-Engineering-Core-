from __future__ import annotations

import asyncio
from pathlib import Path

from aec.methodology.benchmark import REFERENCE_TASKS, LocalBenchmarkRunner
from aec.methodology.contracts import BenchmarkTask, Change, MethodologyProposal
from aec.methodology.governance import (
    MethodologyEvaluator,
    MethodologyGovernance,
    MethodologyRegistry,
    ProposalStore,
    candidate,
)
from aec.persistence.database import Database
from tests.scenarios.test_methodology_evolution import base_version, history


def proposal() -> MethodologyProposal:
    return MethodologyProposal(
        proposal_id="benchmark-proposal",
        methodology_id="dev-adversarial",
        base_version="1.0.0",
        proposed_version="1.1.0",
        problem="Typing errors reach QA",
        hypothesis="A type gate catches errors before QA",
        evidence_refs=("run-021",),
        changes=(
            Change(
                operation="add_quality_gate", gate="type_check", position="before_qa"
            ),
        ),
        expected_metric="acceptance_rate",
        expected_direction="increase",
        risks="The gate could reject valid work.",
        validation_criteria="Run paired reference tasks.",
        rollback_plan="Select version 1.0.0 for new runs.",
    )


def test_controlled_pair_retains_execution_evidence(tmp_path: Path) -> None:
    base = base_version()
    proposed = candidate(base, proposal())
    runs = asyncio.run(
        LocalBenchmarkRunner(tmp_path, repetitions=2).compare(base, proposed)
    )
    assert len(runs) == 8
    assert {(run.task_id, run.arm, run.repetition) for run in runs} == {
        (task, arm, repetition)
        for task in ("valid-types", "invalid-types")
        for arm in ("baseline", "candidate")
        for repetition in (1, 2)
    }
    assert all(Path(run.database_path).is_file() for run in runs)
    assert all(run.failure is None and run.review_iterations is None for run in runs)
    assert all(run.accepted for run in runs if run.task_id == "valid-types")
    assert all(
        run.accepted == (run.arm == "candidate")
        for run in runs
        if run.task_id == "invalid-types"
    )
    assert all(
        run.gate_passed is False and run.agent_invocations == 1
        for run in runs
        if run.task_id == "invalid-types" and run.arm == "candidate"
    )


def _submitted(
    tmp_path: Path, metric: str = "acceptance_rate"
) -> tuple[MethodologyEvaluator, ProposalStore, MethodologyRegistry]:
    db = Database(tmp_path / "aec.sqlite")
    registry = MethodologyRegistry(db, validators=frozenset({"type_check"}))
    registry.seed(base_version(), actor="bootstrap")
    history(db, "run-021")
    store = ProposalStore(db)
    store.submit(proposal().model_copy(update={"expected_metric": metric}))
    return MethodologyEvaluator(store, registry), store, registry


def test_controlled_evaluation_can_support_human_publication(tmp_path: Path) -> None:
    evaluator, store, registry = _submitted(tmp_path)
    result = asyncio.run(
        evaluator.evaluate_controlled(
            "benchmark-proposal",
            LocalBenchmarkRunner(tmp_path / "benchmarks", repetitions=2),
        )
    )
    assert result.verdict == "PASS_WITH_WARNINGS"
    assert result.evidence_kind == "EXECUTED"
    assert (result.baseline, result.candidate) == (0.5, 1.0)
    assert len(result.benchmark_runs) == 8
    assert len(result.pairs) == 4
    assert registry.active("dev-adversarial").version == "1.0.0"
    governance = MethodologyGovernance(
        store, registry, local_approvers=frozenset({"human-test"})
    )
    governance.decide("benchmark-proposal", "human-test", "APPROVED")
    publication = governance.publish("benchmark-proposal", "human-test")
    assert publication.version == "1.1.0"
    assert registry.get("dev-adversarial", "1.0.0").version == "1.0.0"


def test_unavailable_rework_metric_is_inconclusive(tmp_path: Path) -> None:
    evaluator, store, _ = _submitted(tmp_path, "average_review_iterations")
    result = asyncio.run(
        evaluator.evaluate_controlled(
            "benchmark-proposal", LocalBenchmarkRunner(tmp_path / "benchmarks")
        )
    )
    assert result.verdict == "INCONCLUSIVE"
    assert result.pairs == ()
    assert all(run.review_iterations is None for run in result.benchmark_runs)
    assert store.get("benchmark-proposal")[1] == "AWAITING_APPROVAL"


def test_controlled_evaluation_detects_regression(tmp_path: Path) -> None:
    evaluator, _, _ = _submitted(tmp_path)
    tasks = (
        REFERENCE_TASKS[0],
        BenchmarkTask(
            task_id="gate-regression",
            version="1.0.0",
            instructions="Valid work that the new gate incorrectly rejects.",
            acceptance_criteria="QA approves valid work.",
            expected_outcome="qa_pass",
            type_check_passes=False,
        ),
    )
    result = asyncio.run(
        evaluator.evaluate_controlled(
            "benchmark-proposal",
            LocalBenchmarkRunner(tmp_path / "benchmarks", tasks=tasks),
        )
    )
    assert result.verdict == "FAIL"
    assert any("regressed" in check for check in result.checks)


def test_controlled_runner_failure_is_inconclusive(tmp_path: Path) -> None:
    evaluator, _, _ = _submitted(tmp_path)

    class BrokenRunner(LocalBenchmarkRunner):
        async def compare(self, baseline, proposed):  # type: ignore[no-untyped-def]
            raise RuntimeError("benchmark stopped")

    result = asyncio.run(
        evaluator.evaluate_controlled(
            "benchmark-proposal", BrokenRunner(tmp_path / "benchmarks")
        )
    )
    assert result.verdict == "INCONCLUSIVE"
    assert "benchmark stopped" in result.checks[0]
