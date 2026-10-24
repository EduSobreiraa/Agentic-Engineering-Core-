import asyncio
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from aec.communication.broker import Broker
from aec.documentation.renderer import safe
from aec.documentation.worker import DocumentationWorker, request_documentation
from aec.engineering.gates import CommandGate
from aec.engineering.git import commit_hash
from aec.execution.base import ExecutionAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.execution.policy import ExecutionPolicy
from aec.methodologies.arena import arena_comparison, run_arena
from aec.methodologies.definitions import ARENA, ARENA_V101
from aec.methodology.governance import MethodologyRegistry
from aec.methodology.observation import ObservationCollector
from aec.security.identities import Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.models import WorkflowStatus


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


class CandidateAdapter(ExecutionAdapter):
    def __init__(self, solutions: dict[str, str]) -> None:
        self.solutions = solutions
        self.calls: list[tuple[str, str, Path]] = []

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        self.calls.append((request.agent_id, request.prompt, request.working_directory))
        if request.agent_id == "qa":
            solution = json.dumps(
                {
                    "task_id": "arena",
                    "iteration": 1,
                    "reviewed_commit": json.loads(request.prompt)["reviewed_commit"],
                    "review_status": self.solutions.get("qa", "PASSED"),
                    "findings": [],
                }
            )
        else:
            solution = self.solutions[request.agent_id]
        status = (
            ExecutionStatus.FAILED if solution == "ERROR" else ExecutionStatus.SUCCEEDED
        )
        if status == ExecutionStatus.SUCCEEDED and request.agent_id != "qa":
            (request.working_directory / "solution.txt").write_text(solution)
        moment = datetime.now(timezone.utc)
        return ExecutionResult(
            execution_id=request.execution_id,
            agent_id=request.agent_id,
            status=status,
            output=solution,
            started_at=moment,
            finished_at=moment,
        )

    async def cancel(self, execution_id: str) -> bool:
        return False

    async def healthcheck(self) -> bool:
        return True


def setup(
    tmp_path: Path, solutions: dict[str, str]
) -> tuple[WorkflowEngine, Path, Path, CandidateAdapter]:
    repository = tmp_path / "project"
    repository.mkdir()
    git(repository, "init", "-q")
    (repository / "solution.txt").write_text("base\n")
    git(repository, "add", "solution.txt")
    git(
        repository,
        "-c",
        "user.name=AEC",
        "-c",
        "user.email=aec@localhost",
        "commit",
        "-qm",
        "base",
    )
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    broker = Broker(tmp_path / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    agents = {
        name: broker.register(name, Role.IMPLEMENTER)
        for name in ("implementer_a", "implementer_b")
    }
    agents["qa"] = broker.register("qa", Role.QA)
    quality_gate = broker.register("quality-gate", Role.QUALITY_GATE)
    adapter = CandidateAdapter(solutions)
    policy = ExecutionPolicy((tmp_path,))
    manager = ExecutionManager(
        broker,
        {"simulated": adapter},
        [tmp_path],
        policy=policy,
        writable_agents=frozenset(agents),
    )
    engine = WorkflowEngine(
        broker,
        manager,
        orchestrator,
        agents,
        quality_gate,
        repository,
        {},
        local_approvers=frozenset({"supervisor"}),
    )
    return engine, repository, worktrees, adapter


def registry(engine: WorkflowEngine) -> MethodologyRegistry:
    published = MethodologyRegistry(
        engine.broker.db, validators=frozenset({"arena_selected"})
    )
    published.seed(ARENA, actor="trusted-bootstrap")
    return published


@pytest.mark.parametrize(
    ("solutions", "winner"),
    [
        (
            {"implementer_a": "good-fast-small", "implementer_b": "good-slow-big"},
            "implementer_a",
        ),
        ({"implementer_a": "good-fast-big", "implementer_b": "good-slow-small"}, None),
    ],
)
def test_measured_arena_compares_resources_and_informs_qa(
    tmp_path: Path, solutions: dict[str, str], winner: str | None
) -> None:
    engine, repository, worktrees, adapter = setup(tmp_path, solutions)
    published = MethodologyRegistry(
        engine.broker.db, validators=frozenset({"arena_selected"})
    )
    published.seed(ARENA_V101, actor="test-fixture")
    workload = (
        "from pathlib import Path\n"
        "import time\n"
        "kind = Path('solution.txt').read_text()\n"
        "size = 12_000_000 if 'big' in kind else 1_000_000\n"
        "block = bytearray(size)\n"
        "block[:] = b'x' * size\n"
        "time.sleep(0.08 if 'slow' in kind else 0.01)\n"
        "print('same-result')\n"
    )
    result = asyncio.run(
        run_arena(
            engine,
            repository,
            worktrees,
            "measured-arena",
            "Select an efficient good solution",
            ("solution starts with good",),
            ("solution.txt",),
            (
                CommandGate(
                    gate_id="acceptance",
                    argv=(
                        sys.executable,
                        "-c",
                        "from pathlib import Path; "
                        "assert Path('solution.txt').read_text().startswith('good')",
                    ),
                ),
            ),
            executor="simulated",
            registry=published,
            benchmark=CommandGate(
                gate_id="resource-workload",
                argv=(sys.executable, "-c", workload),
                timeout_seconds=2,
            ),
        )
    )
    assert result.winner == winner
    comparison = arena_comparison(engine.broker.db, "measured-arena-approval")
    assert comparison["benchmark_command"] == [sys.executable, "-c", workload]
    recorded = cast(list[dict[str, Any]], comparison["candidates"])
    assert all(len(candidate["benchmark"]) == 5 for candidate in recorded)
    assert all(
        sample["elapsed_ns"] > 0 and sample["peak_rss_kib"] > 0
        for candidate in recorded
        for sample in candidate["benchmark"]
    )
    assert result.approval_status == (
        WorkflowStatus.WAITING_APPROVAL if winner else WorkflowStatus.FAILED
    )
    if winner:
        qa_prompt = json.loads(adapter.calls[-1][1])
        assert qa_prompt["benchmark_command"] == [sys.executable, "-c", workload]
        assert qa_prompt["resource_comparison"]["implementer_a"]["samples"]
        assert "brute-force" in qa_prompt["response_instruction"]
        documenter = engine.broker.register("documenter", Role.DOCUMENTATION)
        worker = DocumentationWorker(engine.broker, documenter)
        request_documentation(
            engine.broker,
            engine.orchestrator,
            documenter,
            "measured-arena-approval",
            "measured-arena",
            documentation_type="arena-report",
        )
        document = worker.process_next()
        assert document is not None
        assert "Tempo e memória por amostra" in worker.get_document(
            document.document_id
        )


def test_measured_arena_qa_can_reject_expensive_solution(tmp_path: Path) -> None:
    engine, repository, worktrees, _ = setup(
        tmp_path,
        {"implementer_a": "good", "implementer_b": "bad", "qa": "INCONCLUSIVE"},
    )
    published = MethodologyRegistry(
        engine.broker.db, validators=frozenset({"arena_selected"})
    )
    published.seed(ARENA_V101, actor="test-fixture")
    result = asyncio.run(
        run_arena(
            engine,
            repository,
            worktrees,
            "measured-qa",
            "spec",
            ("good",),
            ("solution.txt",),
            (
                CommandGate(
                    gate_id="acceptance",
                    argv=(
                        sys.executable,
                        "-c",
                        "from pathlib import Path; "
                        "assert Path('solution.txt').read_text() == 'good'",
                    ),
                ),
            ),
            executor="simulated",
            registry=published,
            benchmark=CommandGate(
                gate_id="resource-workload",
                argv=(sys.executable, "-c", "print('same-result')"),
            ),
        )
    )
    assert result.winner == "implementer_a"
    assert result.qa_status == WorkflowStatus.COMPLETED
    assert result.approval_status == WorkflowStatus.FAILED
    assert (
        arena_comparison(engine.broker.db, "measured-qa-approval")["qa_passed"] is False
    )


@pytest.mark.parametrize(
    ("solutions", "winner"),
    [
        ({"implementer_a": "good-a", "implementer_b": "bad"}, "implementer_a"),
        ({"implementer_a": "good-a", "implementer_b": "good-b"}, None),
        ({"implementer_a": "bad", "implementer_b": "bad"}, None),
        ({"implementer_a": "ERROR", "implementer_b": "good-b"}, "implementer_b"),
    ],
)
def test_sequential_arena_compares_isolated_candidates(
    tmp_path: Path, solutions: dict[str, str], winner: str | None
) -> None:
    engine, repository, worktrees, adapter = setup(tmp_path, solutions)
    base = commit_hash(repository)
    result = asyncio.run(
        run_arena(
            engine,
            repository,
            worktrees,
            "arena-1",
            "Write a good solution",
            ("solution starts with good",),
            ("solution.txt",),
            (
                CommandGate(
                    gate_id="acceptance",
                    argv=(
                        "python",
                        "-c",
                        "from pathlib import Path; "
                        "assert Path('solution.txt').read_text().startswith('good')",
                    ),
                ),
            ),
            executor="simulated",
            registry=registry(engine),
        )
    )
    assert [call[0] for call in adapter.calls[:2]] == [
        "implementer_a",
        "implementer_b",
    ]
    assert adapter.calls[0][1] == adapter.calls[1][1]
    assert adapter.calls[0][2] != adapter.calls[1][2]
    assert result.winner == winner
    assert commit_hash(repository) == base
    assert (repository / "solution.txt").read_text() == "base\n"
    if winner:
        assert adapter.calls[2][0] == "qa"
        selected = next(item for item in result.candidates if item.agent == winner)
        assert (
            json.loads(adapter.calls[2][1])["response_contract"]["reviewed_commit"]
            == selected.commit
        )
        assert result.qa_status == WorkflowStatus.COMPLETED
        assert result.approval_status == WorkflowStatus.WAITING_APPROVAL
        definition = engine.repository.definition("arena-1-approval")
        assert definition.metadata["controller"] == "sequential-arena-v1"
        task_input = engine.repository.task_input("arena-1-approval")
        assert task_input is not None
        assert task_input.requirements == "Write a good solution"
        assert task_input.acceptance_criteria == ("solution starts with good",)
        assert (
            arena_comparison(engine.broker.db, "arena-1-approval")["winner"] == winner
        )
        with engine.broker.db.connect() as connection:
            assert (
                connection.execute(
                    "SELECT version FROM methodology_run_bindings WHERE run_id=?",
                    ("arena-1-approval",),
                ).fetchone()["version"]
                == "1.0.0"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE arena_comparisons SET content='changed' WHERE run_id=?",
                    ("arena-1-approval",),
                )
        with pytest.raises(PermissionError):
            engine.decide("arena-1-approval", "approval", "agent", True)
    else:
        assert result.approval_status == WorkflowStatus.FAILED
    documenter = engine.broker.register("documenter", Role.DOCUMENTATION)
    worker = DocumentationWorker(engine.broker, documenter)
    request_documentation(
        engine.broker,
        engine.orchestrator,
        documenter,
        "arena-1-approval",
        "arena-1",
        documentation_type="arena-report",
    )
    document = worker.process_next()
    assert document is not None
    report = worker.get_document(document.document_id)
    assert "Comparação" in report
    assert f"Vencedor: {safe(winner or 'nenhum')}" in report
    observation = next(
        item
        for item in ObservationCollector(engine.broker.db).collect()
        if item.run_id == "arena-1-approval"
    )
    assert observation.agent_invocations == (3 if winner else 2)


def test_arena_rejects_unsafe_paths_before_execution(tmp_path: Path) -> None:
    engine, repository, worktrees, adapter = setup(
        tmp_path, {"implementer_a": "good-a", "implementer_b": "good-b"}
    )
    with pytest.raises(ValueError, match="allowed paths"):
        asyncio.run(
            run_arena(
                engine,
                repository,
                worktrees,
                "arena-2",
                "spec",
                ("criterion",),
                ("../outside",),
                (CommandGate(gate_id="acceptance", argv=("true",)),),
                executor="simulated",
                registry=registry(engine),
            )
        )
    assert not adapter.calls


def test_arena_blocks_modified_candidate_snapshot(tmp_path: Path) -> None:
    engine, repository, worktrees, _ = setup(
        tmp_path, {"implementer_a": "good-a", "implementer_b": "good-b"}
    )

    class TamperingAdapter(CandidateAdapter):
        async def execute(
            self, request: ExecutionRequest, provider_session_id: str | None = None
        ) -> ExecutionResult:
            if request.agent_id == "implementer_b":
                (worktrees / "arena-3-implementer_a" / "solution.txt").write_text(
                    "tampered"
                )
            return await super().execute(request, provider_session_id)

    engine.manager.adapters["simulated"] = TamperingAdapter(
        {"implementer_a": "good-a", "implementer_b": "good-b"}
    )
    with pytest.raises(RuntimeError, match="candidate snapshot changed"):
        asyncio.run(
            run_arena(
                engine,
                repository,
                worktrees,
                "arena-3",
                "spec",
                ("criterion",),
                ("solution.txt",),
                (CommandGate(gate_id="acceptance", argv=("true",)),),
                executor="simulated",
                registry=registry(engine),
            )
        )
    with pytest.raises(KeyError):
        engine.repository.get_run("arena-3-approval")


def test_arena_requires_independent_qa_pass(tmp_path: Path) -> None:
    engine, repository, worktrees, adapter = setup(
        tmp_path,
        {"implementer_a": "good-a", "implementer_b": "bad", "qa": "INCONCLUSIVE"},
    )
    result = asyncio.run(
        run_arena(
            engine,
            repository,
            worktrees,
            "arena-4",
            "spec",
            ("criterion",),
            ("solution.txt",),
            (
                CommandGate(
                    gate_id="acceptance",
                    argv=(
                        "python",
                        "-c",
                        "from pathlib import Path; "
                        "assert Path('solution.txt').read_text().startswith('good')",
                    ),
                ),
            ),
            executor="simulated",
            registry=registry(engine),
        )
    )
    assert [call[0] for call in adapter.calls] == [
        "implementer_a",
        "implementer_b",
        "qa",
    ]
    assert result.winner == "implementer_a"
    assert result.qa_status == WorkflowStatus.COMPLETED
    assert result.approval_status == WorkflowStatus.FAILED
    assert arena_comparison(engine.broker.db, "arena-4-approval")["qa_passed"] is False
