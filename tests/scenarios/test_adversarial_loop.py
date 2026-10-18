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
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.security.identities import Role


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
        writable_agents=frozenset({"implementer"}),
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
