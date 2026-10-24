import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aec.communication.broker import Broker
from aec.communication.schemas import ArtifactRef
from aec.engineering.gates import CommandGate
from aec.engineering.loop import AdversarialLoop
from aec.execution.base import ExecutionAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import (
    ExecutionBudget,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from aec.execution.policy import ExecutionPolicy
from aec.methodology.contracts import MethodologyVersion
from aec.methodology.governance import MethodologyRegistry
from aec.security.identities import Role
from aec.workflows.models import WorkflowDefinition


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


class SimulatedAgents(ExecutionAdapter):
    def __init__(
        self,
        never_fix: bool = False,
        qa_modify: bool = False,
        high_hypothesis: bool = False,
        fake_evidence: bool = False,
    ) -> None:
        self.never_fix = never_fix
        self.qa_modify = qa_modify
        self.high_hypothesis = high_hypothesis
        self.fake_evidence = fake_evidence
        self.implementations = 0

    async def healthcheck(self) -> bool:
        return True

    async def cancel(self, execution_id: str) -> bool:
        return False

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        context = json.loads(request.prompt)
        if request.agent_id == "implementer":
            self.implementations += 1
            fixed = self.implementations > 1 and not self.never_fix
            (request.working_directory / "calc.py").write_text(
                "def divide(a, b):\n"
                + (
                    "    if b == 0:\n        raise ZeroDivisionError('zero')\n"
                    if fixed
                    else ""
                )
                + "    return a / b\n"
            )
            output = "Implemented division"
        else:
            source = (request.working_directory / "calc.py").read_text()
            if self.qa_modify:
                (request.working_directory / "calc.py").write_text("tampered\n")
            fixed = "if b == 0" in source
            finding = {
                "finding_id": "division-zero",
                "severity": "HIGH",
                "category": "correctness",
                "title": "Zero divisor",
                "description": "Explicit zero divisor handling is missing",
                "file": "calc.py",
                "line": 2,
                "reproduction_steps": ["divide(1, 0)"],
                "expected_behavior": "explicit ZeroDivisionError",
                "actual_behavior": "implicit operator error",
                "status": "REPRODUCED",
            }
            if self.fake_evidence:
                finding["reproduction_steps"] = []
                finding["evidence_refs"] = [
                    {
                        "artifact_id": "missing",
                        "kind": "text",
                        "uri": f"artifact://{request.run_id}/missing",
                        "sha256": "0" * 64,
                        "created_by": "qa",
                    }
                ]
            output = json.dumps(
                {
                    "task_id": "divide",
                    "iteration": context["iteration"],
                    "reviewed_commit": context["reviewed_commit"],
                    "review_status": "PASSED" if fixed else "FAILED",
                    "findings": (
                        [{**finding, "status": "HYPOTHESIS"}]
                        if fixed and self.high_hypothesis
                        else []
                        if fixed
                        else [finding]
                    ),
                }
            )
        moment = datetime.now(timezone.utc)
        return ExecutionResult(
            execution_id=request.execution_id,
            agent_id=request.agent_id,
            status=ExecutionStatus.SUCCEEDED,
            output=output,
            started_at=moment,
            finished_at=moment,
        )


def setup(
    tmp_path: Path,
    never_fix: bool = False,
    limit: int = 2,
    qa_modify: bool = False,
    gate_fail: bool = False,
    high_hypothesis: bool = False,
    fake_evidence: bool = False,
    budget: ExecutionBudget | None = None,
) -> tuple[AdversarialLoop, SimulatedAgents]:
    repository = tmp_path / "project"
    repository.mkdir()
    git(repository, "init", "-q")
    (repository / "calc.py").write_text("def divide(a, b):\n    pass\n")
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_calc.py").write_text(
        "import unittest\nfrom calc import divide\n"
        "class DivisionTest(unittest.TestCase):\n"
        "    def test_zero(self):\n"
        "        with self.assertRaisesRegex(ZeroDivisionError, 'zero'):\n"
        "            divide(1, 0)\n"
        "    def test_result(self):\n"
        "        self.assertEqual(divide(6, 3), 2)\n"
    )
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "base",
    )
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    qa = broker.register("qa", Role.QA)
    quality_gate = broker.register("quality-gate", Role.QUALITY_GATE)
    adapter = SimulatedAgents(never_fix, qa_modify, high_hypothesis, fake_evidence)
    manager = ExecutionManager(
        broker,
        {"simulated": adapter},
        [worktrees],
        policy=ExecutionPolicy((tmp_path,)),
        writable_agents=frozenset({"implementer"}),
        budget=budget,
    )
    loop = AdversarialLoop(
        broker,
        manager,
        orchestrator,
        implementer,
        qa,
        quality_gate,
        repository,
        worktrees,
        (
            CommandGate(
                gate_id="tests",
                argv=("python3", "-c", "raise SystemExit(1)")
                if gate_fail
                else ("python3", "-m", "unittest", "discover", "-s", "tests"),
            ),
        ),
        executor="simulated",
        max_iterations=limit,
        local_approvers=frozenset({"supervisor"}),
    )
    return loop, adapter


def test_operational_policy_bounds_adversarial_iterations(tmp_path: Path) -> None:
    budget = ExecutionBudget(
        version="2", max_concurrent_agents=2, max_invocations_per_run=4,
        max_retries=0, execution_timeout_seconds=120,
        max_duration_seconds=1800, max_iterations=1,
    )
    with pytest.raises(ValueError, match="iteration count exceeds"):
        setup(tmp_path, limit=2, budget=budget)


def setup_published(
    tmp_path: Path, type_check_passes: bool = True
) -> tuple[AdversarialLoop, SimulatedAgents]:
    baseline, adapter = setup(tmp_path)
    definition = WorkflowDefinition.model_validate(
        {
            "id": "dev-adversarial",
            "version": "1.1.0",
            "name": "Adversarial with type check",
            "steps": [
                {
                    "id": "implement",
                    "type": "agent",
                    "agent": "implementer",
                    "executor": "simulated",
                    "inputs": {"prompt": "implement"},
                },
                {
                    "id": "type_check",
                    "type": "validation",
                    "validator": "type_check",
                    "depends_on": ["implement"],
                },
                {
                    "id": "qa",
                    "type": "agent",
                    "agent": "qa",
                    "executor": "simulated",
                    "depends_on": ["implement", "type_check"],
                    "inputs": {"prompt": "review", "artifact_id": "artifact"},
                },
                {"id": "approval", "type": "human_approval", "depends_on": ["qa"]},
            ],
        }
    )
    MethodologyRegistry(
        baseline.broker.db, validators=frozenset({"type_check"})
    ).seed(
        MethodologyVersion(
            methodology_id="dev-adversarial",
            version="1.1.0",
            definition=definition,
            mandatory_gates=frozenset({"type_check"}),
        ),
        actor="test-bootstrap",
    )
    gate = CommandGate(
        gate_id="type_check",
        argv=("python3", "-c", "raise SystemExit(0)")
        if type_check_passes
        else ("python3", "-c", "raise SystemExit(1)"),
    )
    loop = AdversarialLoop(
        baseline.broker,
        baseline.manager,
        baseline.orchestrator,
        baseline.implementer,
        baseline.qa,
        baseline.quality_gate,
        baseline.repository,
        baseline.worktree_root,
        baseline.gates + (gate,),
        executor="simulated",
        max_iterations=2,
        local_approvers=frozenset({"supervisor"}),
        methodology_version="1.1.0",
    )
    return loop, adapter


def test_published_version_runs_type_check_before_qa(tmp_path: Path) -> None:
    loop, adapter = setup_published(tmp_path)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "WAITING_APPROVAL"
    assert adapter.implementations == 2
    with loop.broker.db.connect() as connection:
        binding = connection.execute(
            "SELECT version FROM methodology_run_bindings WHERE run_id=?", (run_id,)
        ).fetchone()
    assert binding["version"] == "1.1.0"
    gate = loop.broker.get_message(f"gate:{run_id}:1:type_check")
    assert gate is not None
    history = loop.history(run_id)["messages"]
    reviews = [message for message in history if message["type"] == "REVIEW_REQUEST"]
    assert reviews and gate.created_at.isoformat() < reviews[0]["created_at"]


def test_failed_published_pre_qa_gate_blocks_review(tmp_path: Path) -> None:
    loop, adapter = setup_published(tmp_path, type_check_passes=False)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "BLOCKED"
    assert adapter.implementations == 1
    assert not any(
        message["type"] == "REVIEW_REQUEST"
        for message in loop.history(run_id)["messages"]
    )


def test_two_iterations_reach_human_approval(tmp_path: Path) -> None:
    loop, adapter = setup(tmp_path)
    run_id = loop.create(
        "divide",
        "Implement divide with explicit zero handling",
        ("zero raises",),
        ("calc.py",),
    )
    assert asyncio.run(loop.run(run_id)) == "WAITING_APPROVAL"
    history = loop.history(run_id)
    assert len(history["iterations"]) == 2
    assert json.loads(history["iterations"][0]["review"])["review_status"] == "FAILED"
    assert json.loads(history["iterations"][1]["review"])["review_status"] == "PASSED"
    assert adapter.implementations == 2
    report = loop.report(run_id)
    assert report["result_commit"] != report["base_commit"]
    assert "raise ZeroDivisionError('zero')" in report["diff"]
    assert report["iterations"][1]["gates"][1]["status"] == "PASSED"
    assert all(
        message["metadata"].get("engineering_run_id") == run_id
        for message in history["messages"]
        if message["type"] in {"TASK_ASSIGNED", "REVIEW_REQUEST"}
    )
    assert loop.broker.get_message(f"rework:{run_id}:1") is not None
    assert loop.broker.get_message(f"approval:{run_id}") is not None
    assert asyncio.run(loop.run(run_id)) == "WAITING_APPROVAL"
    assert adapter.implementations == 2
    with pytest.raises(PermissionError):
        loop.decide(run_id, "implementer", True)
    assert loop.decide(run_id, "supervisor", True) == "APPROVED"
    decision = loop.broker.get_message(f"decision:{run_id}")
    assert decision is not None and decision.type.value == "TASK_COMPLETED"
    assert (tmp_path / "project" / "calc.py").read_text() == (
        "def divide(a, b):\n    pass\n"
    )


def test_second_controller_waits_for_same_run(tmp_path: Path) -> None:
    class HoldingAgents(SimulatedAgents):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            if request.agent_id == "implementer" and self.implementations == 0:
                self.started.set()
                await self.release.wait()
            return await super().execute(request, provider_session_id)

    loop, _ = setup(tmp_path)
    adapter = HoldingAgents()
    loop.manager.adapters["simulated"] = adapter
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))

    async def scenario() -> None:
        first = asyncio.create_task(loop.run(run_id))
        await adapter.started.wait()
        other_manager = ExecutionManager(
            loop.broker,
            {"simulated": adapter},
            [tmp_path / "worktrees"],
            policy=ExecutionPolicy((tmp_path,)),
            writable_agents=frozenset({"implementer"}),
        )
        second_loop = AdversarialLoop(
            loop.broker,
            other_manager,
            loop.orchestrator,
            loop.implementer,
            loop.qa,
            loop.quality_gate,
            loop.repository,
            loop.worktree_root,
            loop.gates,
            executor="simulated",
            max_iterations=2,
            local_approvers=frozenset({"supervisor"}),
        )
        second = asyncio.create_task(second_loop.run(run_id))
        await asyncio.sleep(0.05)
        assert second_loop.history(run_id)["run"]["status"] == "RUNNING"
        with pytest.raises(RuntimeError, match="another controller"):
            await second_loop.cancel(run_id, "supervisor")
        adapter.release.set()
        assert tuple(await asyncio.gather(first, second)) == (
            "WAITING_APPROVAL",
            "WAITING_APPROVAL",
        )

    asyncio.run(scenario())
    assert adapter.implementations == 2


def test_iteration_limit_blocks(tmp_path: Path) -> None:
    loop, adapter = setup(tmp_path, never_fix=True)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "BLOCKED"
    assert adapter.implementations == 2
    assert len(loop.history(run_id)["iterations"]) == 2
    assert loop.broker.get_message(f"approval:{run_id}") is None


def test_qa_snapshot_mutation_blocks(tmp_path: Path) -> None:
    loop, _ = setup(tmp_path, qa_modify=True)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "BLOCKED"
    assert loop.broker.get_message(f"approval:{run_id}") is None


def test_nonexistent_qa_evidence_blocks(tmp_path: Path) -> None:
    loop, _ = setup(tmp_path, fake_evidence=True)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "BLOCKED"
    assert loop.broker.get_message(f"approval:{run_id}") is None
    assert "nonexistent or altered evidence" in loop.history(run_id)["run"]["error"]


def test_gate_failure_blocks_even_after_qa_pass(tmp_path: Path) -> None:
    loop, _ = setup(tmp_path, gate_fail=True)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "BLOCKED"
    history = loop.history(run_id)
    assert json.loads(history["iterations"][1]["review"])["review_status"] == "PASSED"
    assert json.loads(history["iterations"][1]["gates"])[1]["status"] == "FAILED"
    assert loop.broker.get_message(f"approval:{run_id}") is None


def test_unverified_high_hypothesis_blocks(tmp_path: Path) -> None:
    loop, _ = setup(tmp_path, high_hypothesis=True)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "BLOCKED"
    assert loop.broker.get_message(f"approval:{run_id}") is None


def test_duration_limit_blocks_before_execution(tmp_path: Path) -> None:
    loop, adapter = setup(tmp_path)
    loop.max_duration_seconds = 0.001
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    import time

    time.sleep(0.01)
    assert asyncio.run(loop.run(run_id)) == "BLOCKED"
    assert adapter.implementations == 0


def test_history_and_approval_survive_restart(tmp_path: Path) -> None:
    loop, _ = setup(tmp_path)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "WAITING_APPROVAL"
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    qa = broker.register("qa", Role.QA)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    manager = ExecutionManager(
        broker,
        {"simulated": SimulatedAgents()},
        [tmp_path / "worktrees"],
        policy=ExecutionPolicy((tmp_path,)),
        writable_agents=frozenset({"implementer"}),
    )
    restarted = AdversarialLoop(
        broker,
        manager,
        orchestrator,
        implementer,
        qa,
        gate,
        tmp_path / "project",
        tmp_path / "worktrees",
        loop.gates,
        executor="simulated",
        max_iterations=2,
        local_approvers=frozenset({"supervisor"}),
    )
    assert len(restarted.history(run_id)["messages"]) >= 10
    assert (
        restarted.report(run_id)["result_commit"]
        == loop.report(run_id)["result_commit"]
    )
    assert restarted.decide(run_id, "supervisor", True) == "APPROVED"


def test_interruption_before_correction_resumes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop, adapter = setup(tmp_path)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    original = loop._agent
    interrupted = False

    async def interrupt_once(
        current_run: str,
        number: int,
        stage: str,
        path: Path,
        prompt: str,
        artifact_id: str = "",
    ) -> tuple[str, list[ArtifactRef]]:
        nonlocal interrupted
        if number == 2 and stage == "implement" and not interrupted:
            interrupted = True
            raise asyncio.CancelledError
        return await original(current_run, number, stage, path, prompt, artifact_id)

    monkeypatch.setattr(loop, "_agent", interrupt_once)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run(run_id))
    assert adapter.implementations == 1
    monkeypatch.setattr(loop, "_agent", original)
    assert asyncio.run(loop.run(run_id)) == "WAITING_APPROVAL"
    assert adapter.implementations == 2


def test_commit_captured_before_database_failure_is_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, adapter = setup(tmp_path)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    original = loop._save

    def fail_before_save(*args: object, **kwargs: object) -> None:
        if len(args) >= 3 and args[2] == "IMPLEMENTED":
            raise KeyboardInterrupt("database unavailable after commit")
        original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(loop, "_save", fail_before_save)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(loop.run(run_id))
    assert adapter.implementations == 1
    assert loop.history(run_id)["iterations"][0]["status"] == "PREPARED"
    broker = Broker(tmp_path / "aec.sqlite")
    manager = ExecutionManager(
        broker,
        {"simulated": adapter},
        [tmp_path / "worktrees"],
        policy=ExecutionPolicy((tmp_path,)),
        writable_agents=frozenset({"implementer"}),
    )
    resumed = AdversarialLoop(
        broker,
        manager,
        broker.register("orchestrator", Role.ORCHESTRATOR),
        broker.register("implementer", Role.IMPLEMENTER),
        broker.register("qa", Role.QA),
        broker.register("quality-gate", Role.QUALITY_GATE),
        tmp_path / "project",
        tmp_path / "worktrees",
        loop.gates,
        executor="simulated",
        max_iterations=2,
        local_approvers=frozenset({"supervisor"}),
    )
    assert asyncio.run(resumed.run(run_id)) == "WAITING_APPROVAL"
    assert adapter.implementations == 2


def test_unrelated_commit_is_not_recovered(tmp_path: Path) -> None:
    loop, adapter = setup(tmp_path)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    worktree = Path(loop.history(run_id)["run"]["worktree_path"])
    (worktree / "calc.py").write_text("def divide(a, b):\n    return 0\n")
    git(worktree, "add", "calc.py")
    git(
        worktree,
        "-c",
        "user.name=Other",
        "-c",
        "user.email=other@localhost",
        "commit",
        "-qm",
        "unrelated change",
    )
    assert asyncio.run(loop.run(run_id)) == "BLOCKED"
    assert adapter.implementations == 0


def test_artifact_change_after_validation_prevents_approval(tmp_path: Path) -> None:
    loop, _ = setup(tmp_path)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "WAITING_APPROVAL"
    worktree = Path(loop.history(run_id)["run"]["worktree_path"])
    (worktree / "calc.py").write_text("tampered\n")
    with pytest.raises(ValueError, match="artifact changed"):
        loop.decide(run_id, "supervisor", True)
    assert loop.history(run_id)["run"]["status"] == "BLOCKED"


def test_human_rejection_records_failure(tmp_path: Path) -> None:
    loop, _ = setup(tmp_path)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "WAITING_APPROVAL"
    assert loop.decide(run_id, "supervisor", False) == "REJECTED"
    decision = loop.broker.get_message(f"decision:{run_id}")
    assert decision is not None and decision.type.value == "TASK_FAILED"


def test_human_cancellation_closes_pending_approval(tmp_path: Path) -> None:
    loop, _ = setup(tmp_path)
    run_id = loop.create("divide", "Fix zero handling", ("zero raises",), ("calc.py",))
    assert asyncio.run(loop.run(run_id)) == "WAITING_APPROVAL"
    with pytest.raises(PermissionError):
        asyncio.run(loop.cancel(run_id, "implementer"))
    assert asyncio.run(loop.cancel(run_id, "supervisor")) == "CANCELLED"
    assert loop.broker.get_message(f"cancelled:{run_id}") is not None
    with pytest.raises(ValueError):
        loop.decide(run_id, "supervisor", True)
