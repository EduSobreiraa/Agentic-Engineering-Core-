"""Three paired read-only Codex tasks with optional pstack writing guidance."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal, cast

from aec.communication.broker import Broker
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import (
    CodexSettings,
    ExecutionBudget,
    ExecutionResult,
    ExecutionStatus,
)
from aec.execution.policy import ExecutionPolicy
from aec.methodologies.skills import (
    SkillRegistry,
    SkillState,
    inspect_local_skill,
)
from aec.methodology.contracts import BenchmarkRun
from aec.methodology.governance import MethodologyEvaluator
from aec.security.identities import Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.loader import definition_hash
from aec.workflows.models import WorkflowDefinition, WorkflowLimits, WorkflowStep
from scripts.phase8_skill_experiment import enable_via_gateway


@dataclass(frozen=True)
class Task:
    identifier: str
    prompt: str
    required: tuple[str, ...]
    forbidden: tuple[str, ...]

    def accepts(self, output: str) -> bool:
        text = output.strip().lower()
        return (
            0 < len(text) <= 200
            and all(word in text for word in self.required)
            and not any(word in text for word in self.forbidden)
        )

    def acceptance_sha256(self) -> str:
        return digest(json.dumps({
            "required": self.required, "forbidden": self.forbidden,
            "max_characters": 200,
        }, sort_keys=True))


TASKS = (
    Task(
        "migration",
        "Rewrite in plain English, one sentence only: The team leveraged a "
        "multifaceted approach to facilitate the migration. "
        "Keep the word migration.",
        ("team", "migration"), ("leveraged", "facilitate", "multifaceted"),
    ),
    Task(
        "active-voice",
        "Rewrite in active voice, one sentence only: The report was reviewed "
        "by the team yesterday.",
        ("team", "report", "review"), ("was reviewed",),
    ),
    Task(
        "concise-action",
        "Rewrite concisely, one sentence only: In order to begin the test, "
        "we need to make a decision about the configuration.",
        ("test", "configuration"), ("in order to", "make a decision"),
    ),
)
Arm = Literal["baseline", "candidate"]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def run_arm(
    directory: Path, task: Task, arm: Arm, registry: SkillRegistry,
    model: str, skill_sha256: str,
) -> tuple[BenchmarkRun, dict[str, object]]:
    directory.mkdir(parents=True)
    workspace = directory / "workspace"
    workspace.mkdir()
    database = directory / "aec.sqlite"
    broker = Broker(database)
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    policy = ExecutionPolicy((workspace,))
    budget = ExecutionBudget(
        version="2", max_concurrent_agents=1, max_invocations_per_run=1,
        max_retries=0, execution_timeout_seconds=180,
        max_duration_seconds=300, max_iterations=1,
    )
    manager = ExecutionManager(
        broker, {"codex": CodexCliAdapter(policy=policy)}, [workspace],
        policy=policy, max_concurrency=1,
        budget=budget,
        model_by_agent={implementer.agent_id: CodexSettings(
            model=model, reasoning_effort="medium"
        )},
    )
    definition = WorkflowDefinition(
        id="phase8-skill-experiment", version="1.0.0",
        name="Plain writing benchmark",
        limits=WorkflowLimits(max_parallel_steps=1, max_total_steps=2,
                              max_duration_seconds=300),
        steps=[
            WorkflowStep(
                id="rewrite", type="agent", agent="implementer",
                executor="codex", inputs={"prompt": task.prompt},
                timeout_seconds=180,
            ),
            WorkflowStep(
                id="gate", type="validation", validator="text_gate",
                depends_on=["rewrite"],
                inputs={"text": "{{rewrite.output}}"},
            ),
        ],
    )
    engine = WorkflowEngine(
        broker, manager, orchestrator, {"implementer": implementer}, gate,
        workspace, {"text_gate": lambda inputs: task.accepts(inputs["text"])},
        skill_registry=registry if arm == "candidate" else None,
    )
    run_id = f"{task.identifier}-{arm}"
    conditions_sha256 = digest(json.dumps({
        "task": task.prompt, "model": model, "effort": "medium",
        "sandbox": "read-only", "limits": budget.model_dump(),
    }, sort_keys=True))
    status = "ERROR"
    failure: str | None = None
    started = perf_counter()
    try:
        engine.create(
            definition, run_id,
            skills={"rewrite": ("pstack:unslop",)}
            if arm == "candidate" else None,
        )
        status = (await engine.run(run_id)).status.value
    except Exception as exc:
        failure = type(exc).__name__
    duration = perf_counter() - started
    steps = engine.repository.steps(run_id) if failure is None else {}
    rewrite = steps.get("rewrite")
    validation = steps.get("gate")
    output = (rewrite.result or {}).get("output") if rewrite else None
    gate_passed = (validation.result or {}).get("passed") if validation else None
    with broker.db.connect() as db:
        executions = db.execute(
            "SELECT execution_id FROM executions WHERE run_id=?", (run_id,)
        ).fetchall()
        settings = db.execute(
            "SELECT s.requested_model,s.reasoning_effort,s.effective_model "
            "FROM execution_settings s JOIN executions e "
            "ON e.execution_id=s.execution_id WHERE e.run_id=?", (run_id,)
        ).fetchone()
    results = [manager.repository.get(row["execution_id"]) for row in executions]
    use = [result.usage for result in results
           if isinstance(result, ExecutionResult)]
    if settings is None or settings["requested_model"] != model:
        failure = failure or "model request was not persisted"
    if any(
        not isinstance(result, ExecutionResult)
        or result.status != ExecutionStatus.SUCCEEDED
        for result in results
    ):
        failure = failure or "execution did not succeed"
    record = BenchmarkRun(
        task_id=task.identifier, repetition=1, arm=arm, run_id=run_id,
        methodology_version=definition.version,
        definition_sha256=definition_hash(definition),
        executed_definition_sha256=definition_hash(definition),
        acceptance_sha256=task.acceptance_sha256(),
        conditions_sha256=conditions_sha256, workflow_status=status,
        accepted=status == "COMPLETED" and gate_passed is True,
        gate_passed=gate_passed if isinstance(gate_passed, bool) else None,
        review_iterations=None, duration_seconds=duration,
        agent_invocations=len(executions), database_path=str(database),
        workspace_path=str(workspace), failure=failure,
    )
    extra = {
        "prompt_sha256": digest(task.prompt),
        "output": output,
        "output_sha256": digest(output) if isinstance(output, str) else None,
        "requested_model": settings["requested_model"] if settings else None,
        "effective_model": settings["effective_model"] if settings else None,
        "reasoning_effort": settings["reasoning_effort"] if settings else None,
        "skill_sha256": skill_sha256 if arm == "candidate" else None,
        "usage": use,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
    }
    return record, extra


async def compare(root: Path, model: str, registry: SkillRegistry,
                  skill_sha256: str) -> dict[str, object]:
    records: list[BenchmarkRun] = []
    details: list[dict[str, object]] = []
    for index, task in enumerate(TASKS):
        arms: tuple[Arm, Arm] = ("baseline", "candidate") if index % 2 == 0 else (
            "candidate", "baseline"
        )
        for arm in arms:
            record, extra = await run_arm(
                root / task.identifier / arm, task, arm, registry, model,
                skill_sha256,
            )
            records.append(record)
            details.append({**record.model_dump(mode="json"), **extra})
    pairs = MethodologyEvaluator._controlled_pairs(
        tuple(records), "acceptance_rate"
    )
    if len(pairs) != len(TASKS):
        raise RuntimeError("benchmark pairs are incomplete")
    return {
        "classification": "INCONCLUSIVE",
        "reason": (
            "One run per arm and three writing tasks cannot establish superiority."
        ),
        "methodology_id": "phase8-skill-experiment",
        "methodology_version": "1.0.0",
        "intervention": "pstack:unslop",
        "skill_sha256": skill_sha256,
        "requested_model": model,
        "outcomes": {arm: {
            "accepted": sum(run.accepted for run in records if run.arm == arm),
            "runs": sum(run.arm == arm for run in records),
            "invocations": sum(
                run.agent_invocations for run in records if run.arm == arm
            ),
        } for arm in ("baseline", "candidate")},
        "pairs": [pair.model_dump(mode="json") for pair in pairs],
        "runs": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skill-file", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--approver", required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    reference = inspect_local_skill(
        "pstack:unslop", args.skill_file, args.skill_file.parent.parent,
        compatible_executors=frozenset({"codex"}),
        required_permissions=frozenset({"read-only"}),
    )
    registry = SkillRegistry(frozenset({args.approver}))
    registry.register(reference)
    if registry.validate(
        reference.identifier, "codex", frozenset({"read-only"})
    ) != SkillState.VALIDATED:
        raise RuntimeError("skill is not validated")
    gateway = enable_via_gateway(root / "approvals.sock", registry, args.approver)
    report = asyncio.run(compare(root, args.model, registry, reference.sha256))
    report["gateway"] = gateway
    report["git_commit"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
        text=True, timeout=5,
    ).stdout.strip()
    (root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({
        "classification": report["classification"],
        "outcomes": report["outcomes"],
        "failures": sum(
            run["failure"] is not None
            for run in cast(list[dict[str, object]], report["runs"])
        ),
        "report": str(root / "report.json"),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
