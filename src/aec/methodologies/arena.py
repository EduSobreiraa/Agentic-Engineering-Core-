"""Sequential, supervised comparison of two implementations in trusted projects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from aec.communication.schemas import ArtifactRef
from aec.engineering.gates import CommandGate, GateResult, run_gates
from aec.engineering.git import capture, changed_files, commit_hash, create_worktree
from aec.engineering.models import QAReviewResult
from aec.methodology.governance import MethodologyRegistry
from aec.persistence.database import Database
from aec.security.identities import Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.models import WorkflowDefinition, WorkflowStatus, WorkflowTaskInput

from .arena_benchmark import BenchmarkSample, dominates, measure


@dataclass(frozen=True)
class Candidate:
    agent: str
    worktree: Path
    commit: str | None
    gates: tuple[GateResult, ...]
    error: str | None = None
    benchmark: tuple[BenchmarkSample, ...] = ()
    benchmark_error: str | None = None

    @property
    def valid(self) -> bool:
        return (
            self.commit is not None
            and bool(self.gates)
            and all(gate.status == "PASSED" for gate in self.gates)
        )


@dataclass(frozen=True)
class ArenaResult:
    run_id: str
    base_commit: str
    candidates: tuple[Candidate, Candidate]
    winner: str | None
    qa_status: WorkflowStatus | None
    approval_status: WorkflowStatus | None


def arena_comparison(db: Database, run_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT content,sha256 FROM arena_comparisons WHERE run_id=?", (run_id,)
        ).fetchone()
    if (
        row is None
        or hashlib.sha256(row["content"].encode()).hexdigest() != row["sha256"]
    ):
        raise ValueError("Arena comparison is missing or invalid")
    comparison = json.loads(row["content"])
    if not isinstance(comparison, dict):
        raise ValueError("Arena comparison has an invalid shape")
    return comparison


async def run_arena(
    engine: WorkflowEngine,
    repository: Path,
    worktree_root: Path,
    run_id: str,
    specification: str,
    criteria: tuple[str, ...],
    allowed_paths: tuple[str, ...],
    gates: tuple[CommandGate, ...],
    *,
    registry: MethodologyRegistry,
    executor: str,
    agents: tuple[str, str] = ("implementer_a", "implementer_b"),
    artifact_refs: tuple[ArtifactRef, ...] = (),
    benchmark: CommandGate | None = None,
) -> ArenaResult:
    """Run each candidate once, compare required gates, then request approval."""
    published = registry.active("implementation-arena")
    controller = published.definition.metadata.get("controller")
    if (published.version, controller) not in {
        ("1.0.0", "sequential-arena-v1"),
        ("1.0.1", "sequential-arena-v2"),
    }:
        raise ValueError("unsupported published Arena version")
    measured = published.version == "1.0.1"
    if measured and (benchmark is None or not benchmark.argv):
        raise ValueError("Arena 1.0.1 requires a benchmark command")
    if not measured and benchmark is not None:
        raise ValueError("Arena 1.0.0 does not support resource comparison")
    if Path(registry.db.path).resolve() != Path(engine.broker.db.path).resolve():
        raise ValueError("Arena registry and engine must share a database")
    if not specification or not criteria or not gates or not allowed_paths:
        raise ValueError("specification, criteria, gates and allowed paths required")
    if len(set(agents)) != 2 or any(
        agent not in engine.agents or engine.agents[agent].role != Role.IMPLEMENTER
        for agent in agents
    ):
        raise ValueError("two distinct registered implementers required")
    if not run_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("run_id contains unsafe characters")
    if any(
        not path or Path(path).is_absolute() or ".." in Path(path).parts
        for path in allowed_paths
    ):
        raise ValueError("allowed paths must be relative and confined")
    repository = repository.resolve(strict=True)
    worktree_root = worktree_root.resolve(strict=True)
    engine.manager.policy.require(repository)
    engine.manager.policy.require(worktree_root)
    if repository == worktree_root or repository in worktree_root.parents:
        raise ValueError("worktrees must be outside the main repository")
    if changed_files(repository):
        raise ValueError("repository must be clean")
    base = commit_hash(repository)
    candidates: list[Candidate] = []
    prompt = json.dumps(
        {
            "specification": specification,
            "criteria": criteria,
            "artifact_refs": [ref.model_dump(mode="json") for ref in artifact_refs],
        }
    )
    for agent in agents:
        path = worktree_root / f"{run_id}-{agent}"
        create_worktree(repository, path, base)
        candidate_engine = WorkflowEngine(
            engine.broker,
            engine.manager,
            engine.orchestrator,
            engine.agents,
            engine.quality_gate,
            path,
            engine.validators,
            workspaces={agent: path},
            local_approvers=engine.local_approvers,
        )
        definition = WorkflowDefinition.model_validate(
            {
                "id": "arena-candidate",
                "version": "1.0.0",
                "name": "Implementation Arena candidate",
                "metadata": {"arena_run_id": run_id, "base_commit": base},
                "steps": [
                    {
                        "id": agent,
                        "type": "agent",
                        "agent": agent,
                        "executor": executor,
                        "sandbox_mode": "workspace-write",
                        "inputs": {"prompt": prompt},
                    }
                ],
            }
        )
        candidate_engine.create(definition, f"{run_id}-{agent}")
        run = await candidate_engine.run(f"{run_id}-{agent}")
        if run.status != WorkflowStatus.COMPLETED:
            candidates.append(Candidate(agent, path, None, (), run.status.value))
            if commit_hash(repository) != base or changed_files(repository):
                raise RuntimeError("source repository changed during arena")
            continue
        try:
            commit, _ = capture(path, allowed_paths)
            results = tuple(
                run_gates(path, commit, gates, policy=engine.manager.policy)
            )
            candidates.append(Candidate(agent, path, commit, results))
        except (ValueError, PermissionError, RuntimeError) as exc:
            candidates.append(Candidate(agent, path, None, (), str(exc)))
        if commit_hash(repository) != base or changed_files(repository):
            raise RuntimeError("source repository changed during arena")
    if any(
        candidate.commit is not None
        and (
            commit_hash(candidate.worktree) != candidate.commit
            or changed_files(candidate.worktree)
        )
        for candidate in candidates
    ):
        raise RuntimeError("candidate snapshot changed after evaluation")
    if measured:
        assert benchmark is not None
        for _ in range(5):
            for index, candidate in enumerate(candidates):
                if not candidate.valid or candidate.benchmark_error is not None:
                    continue
                try:
                    assert candidate.commit is not None
                    sample = measure(
                        candidate.worktree,
                        candidate.commit,
                        benchmark,
                        engine.manager.policy,
                    )
                    candidates[index] = replace(
                        candidate, benchmark=(*candidate.benchmark, sample)
                    )
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    candidates[index] = replace(
                        candidate, benchmark_error=f"{type(exc).__name__}: {exc}"
                    )
        if any(
            candidate.commit is not None
            and (
                commit_hash(candidate.worktree) != candidate.commit
                or changed_files(candidate.worktree)
            )
            for candidate in candidates
        ):
            raise RuntimeError("candidate snapshot changed during benchmark")
    valid = [
        candidate
        for candidate in candidates
        if candidate.valid
        and (
            not measured
            or (
                len(candidate.benchmark) == 5
                and len({s.stdout_sha256 for s in candidate.benchmark}) == 1
            )
        )
    ]
    winner = valid[0].agent if len(valid) == 1 else None
    if measured and len(valid) == 2:
        first, second = valid
        if dominates(first.benchmark, second.benchmark):
            winner = first.agent
        elif dominates(second.benchmark, first.benchmark):
            winner = second.agent
    qa_status = None
    approval_status = None
    if winner is not None:
        if "qa" not in engine.agents or engine.agents["qa"].role != Role.QA:
            raise ValueError("independent QA agent required")
        selected = next(candidate for candidate in valid if candidate.agent == winner)
        assert selected.commit is not None
        qa_path = worktree_root / f"{run_id}-qa"
        create_worktree(repository, qa_path, selected.commit)
        qa_engine = WorkflowEngine(
            engine.broker,
            engine.manager,
            engine.orchestrator,
            engine.agents,
            engine.quality_gate,
            qa_path,
            engine.validators,
            workspaces={"qa": qa_path},
            local_approvers=engine.local_approvers,
        )
        resource_context = (
            {
                "benchmark_command": list(benchmark.argv) if benchmark else None,
                "resource_comparison": {
                    candidate.agent: {
                        "samples": [sample.record() for sample in candidate.benchmark],
                        "error": candidate.benchmark_error,
                    }
                    for candidate in candidates
                }
            }
            if measured
            else {}
        )
        qa_definition = WorkflowDefinition.model_validate(
            {
                "id": "arena-qa",
                "version": "1.0.0",
                "name": "Implementation Arena independent QA",
                "metadata": {
                    "arena_run_id": run_id,
                    "reviewed_commit": selected.commit,
                },
                "steps": [
                    {
                        "id": "qa",
                        "type": "agent",
                        "agent": "qa",
                        "executor": executor,
                        "inputs": {
                            "prompt": json.dumps(
                                {
                                    "specification": specification,
                                    "criteria": criteria,
                                    "artifact_refs": [
                                        ref.model_dump(mode="json")
                                        for ref in artifact_refs
                                    ],
                                    "reviewed_commit": selected.commit,
                                    **resource_context,
                                    "response_contract": {
                                        "task_id": "arena",
                                        "iteration": 1,
                                        "reviewed_commit": selected.commit,
                                        "review_status": "PASSED or INCONCLUSIVE",
                                        "findings": [],
                                    },
                                    "response_instruction": (
                                        "Inspect the selected snapshot and respond "
                                        "with only a JSON object matching "
                                        "response_contract. Use INCONCLUSIVE if "
                                        "evidence is insufficient."
                                        + (
                                            " Inspect algorithmic complexity and "
                                            "resource samples. If brute-force "
                                            "behavior is plausible, report a "
                                            "finding and use INCONCLUSIVE."
                                            if measured
                                            else ""
                                        )
                                    ),
                                }
                            ),
                            "artifact_id": selected.commit,
                        },
                    }
                ],
            }
        )
        qa_engine.create(qa_definition, f"{run_id}-qa")
        qa_status = (await qa_engine.run(f"{run_id}-qa")).status
        qa_step = qa_engine.repository.steps(f"{run_id}-qa")["qa"]
        qa_output = qa_step.result.get("output") if qa_step.result else None
        try:
            review = (
                QAReviewResult.model_validate_json(qa_output)
                if isinstance(qa_output, str)
                else None
            )
        except ValueError:
            review = None
        qa_passed = not (
            qa_status != WorkflowStatus.COMPLETED
            or review is None
            or review.reviewed_commit != selected.commit
            or review.review_status != "PASSED"
            or commit_hash(qa_path) != selected.commit
            or changed_files(qa_path)
            or any(
                gate.status != "PASSED"
                for gate in run_gates(
                    qa_path, selected.commit, gates, policy=engine.manager.policy
                )
            )
        )
    else:
        qa_passed = False
    parent_id = f"{run_id}-approval"
    comparison = {
        "base_commit": base,
        "criteria": list(criteria),
        "specification": specification,
        "artifact_refs": [ref.model_dump(mode="json") for ref in artifact_refs],
        "candidates": [
            {
                "agent": candidate.agent,
                "commit": candidate.commit,
                "gates": [gate.model_dump(mode="json") for gate in candidate.gates],
                "error": candidate.error,
                "benchmark": [sample.record() for sample in candidate.benchmark],
                "benchmark_error": candidate.benchmark_error,
            }
            for candidate in candidates
        ],
        "winner": winner,
        "benchmark_command": list(benchmark.argv) if benchmark else None,
        "selection_rule": (
            "nonoverlapping time and memory samples with equal output"
            if measured
            else "single candidate passing required gates"
        ),
        "qa_run_id": f"{run_id}-qa" if qa_status is not None else None,
        "qa_status": qa_status.value if qa_status is not None else None,
        "qa_passed": qa_passed,
    }
    content = json.dumps(comparison, sort_keys=True)
    selection_engine = WorkflowEngine(
        engine.broker,
        engine.manager,
        engine.orchestrator,
        engine.agents,
        engine.quality_gate,
        engine.workspace,
        {
            **engine.validators,
            "arena_selected": lambda _: bool(
                arena_comparison(engine.broker.db, parent_id).get("qa_passed")
            ),
        },
        local_approvers=engine.local_approvers,
    )
    selection_engine.create_published(
        registry,
        "implementation-arena",
        published.version,
        parent_id,
        task_input=WorkflowTaskInput(
            requirements=specification,
            acceptance_criteria=criteria,
            artifact_refs=artifact_refs,
        ),
    )
    with engine.broker.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO arena_comparisons VALUES (?,?,?)",
            (parent_id, content, hashlib.sha256(content.encode()).hexdigest()),
        )
    approval_status = (await selection_engine.run(parent_id)).status
    return ArenaResult(
        run_id, base, (candidates[0], candidates[1]), winner, qa_status, approval_status
    )
