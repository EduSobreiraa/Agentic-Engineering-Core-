"""Run the four-step workflow with a simulated adapter by default."""

from __future__ import annotations

import argparse
import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from aec.communication.broker import Broker
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.base import ExecutionAdapter
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from aec.security.identities import Role

from .engine import WorkflowEngine
from .loader import load
from .models import WorkflowDefinition


class SimulatedAdapter(ExecutionAdapter):
    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        moment = datetime.now(timezone.utc)
        output = (
            "Define add(a, b) and return their sum."
            if request.task_id == "analyze"
            else "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
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


def validate_add_artifact(inputs: dict[str, str]) -> bool:
    try:
        module = ast.parse(inputs["artifact"])
    except SyntaxError:
        return False
    if len(module.body) != 1 or not isinstance(module.body[0], ast.FunctionDef):
        return False
    function = module.body[0]
    if (
        function.name != "add"
        or [arg.arg for arg in function.args.args] != ["a", "b"]
        or any(
            not isinstance(arg.annotation, ast.Name) or arg.annotation.id != "int"
            for arg in function.args.args
        )
        or not isinstance(function.returns, ast.Name)
        or function.returns.id != "int"
        or function.decorator_list
        or function.args.defaults
        or function.args.kw_defaults
        or function.args.kwonlyargs
        or function.args.posonlyargs
        or function.args.vararg
        or function.args.kwarg
    ):
        return False
    if len(function.body) != 1 or not isinstance(function.body[0], ast.Return):
        return False
    expression = function.body[0].value
    return (
        isinstance(expression, ast.BinOp)
        and isinstance(expression.op, ast.Add)
        and isinstance(expression.left, ast.Name)
        and expression.left.id == "a"
        and isinstance(expression.right, ast.Name)
        and expression.right.id == "b"
    )


async def run_demo(
    database: Path, workspace: Path, *, real_codex: bool = False, approve: bool = False
) -> tuple[str, str]:
    workspace.mkdir(parents=True, exist_ok=True)
    broker = Broker(database)
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    gate = broker.register("quality-gate", Role.QUALITY_GATE)
    agents = {
        name: broker.register(name, Role.IMPLEMENTER)
        for name in ("analyst", "implementer")
    }
    executor = "codex" if real_codex else "simulated"
    adapter: ExecutionAdapter = CodexCliAdapter() if real_codex else SimulatedAdapter()
    manager = ExecutionManager(broker, {executor: adapter}, [workspace])
    engine = WorkflowEngine(
        broker,
        manager,
        orchestrator,
        agents,
        gate,
        workspace,
        {"artifact-check": validate_add_artifact},
        local_approvers=frozenset({"local-supervisor"}),
    )
    example = Path(__file__).resolve().parents[3] / "docs/workflows/example.yaml"
    definition: WorkflowDefinition = load(example)
    if real_codex:
        definition = definition.model_copy(
            update={
                "steps": [
                    step.model_copy(update={"executor": "codex"})
                    if step.type == "agent"
                    else step
                    for step in definition.steps
                ]
            }
        )
    run = engine.create(definition)
    state = await engine.run(run.run_id)
    if approve and state.status.value == "WAITING_APPROVAL":
        engine.decide(run.run_id, "approval", "local-supervisor", True)
        state = await engine.run(run.run_id)
    return run.run_id, state.status.value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the declarative workflow")
    parser.add_argument(
        "--database", type=Path, default=Path("/tmp/aec-workflow.sqlite")
    )
    parser.add_argument("--workspace", type=Path, default=Path("/tmp/aec-workflow"))
    parser.add_argument(
        "--real-codex",
        action="store_true",
        help="invoke real Codex CLI turns; consumes subscription capacity",
    )
    parser.add_argument("--approve", action="store_true", help="approve locally")
    args = parser.parse_args()
    run_id, status = asyncio.run(
        run_demo(
            args.database,
            args.workspace,
            real_codex=args.real_codex,
            approve=args.approve,
        )
    )
    print(f"run_id={run_id} status={status}")


if __name__ == "__main__":
    main()
