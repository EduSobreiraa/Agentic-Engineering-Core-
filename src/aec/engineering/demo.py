from __future__ import annotations

import argparse
import asyncio
import subprocess
import tempfile
from pathlib import Path

from aec.communication.broker import Broker
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.manager import ExecutionManager
from aec.security.identities import Role

from .gates import CommandGate
from .loop import AdversarialLoop


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        timeout=30,
    )


async def run_demo(root: Path) -> tuple[str, str]:
    root.mkdir(parents=True, exist_ok=False)
    repository = root / "project"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / "calc.py").write_text(
        "def divide(a: float, b: float) -> float:\n    raise NotImplementedError\n"
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_calc.py").write_text(
        "import unittest\nfrom calc import divide\n"
        "class DivisionTest(unittest.TestCase):\n"
        "    def test_result(self):\n"
        "        self.assertEqual(divide(6, 3), 2)\n"
        "    def test_zero(self):\n"
        "        with self.assertRaisesRegex(ZeroDivisionError, 'zero'):\n"
        "            divide(1, 0)\n"
    )
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=AEC",
        "-c",
        "user.email=aec@localhost",
        "commit",
        "-qm",
        "initial test project",
    )
    worktrees = root / "worktrees"
    worktrees.mkdir()
    broker = Broker(root / "aec.sqlite")
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    qa = broker.register("qa", Role.QA)
    quality_gate = broker.register("quality-gate", Role.QUALITY_GATE)
    manager = ExecutionManager(
        broker,
        {"codex": CodexCliAdapter()},
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
                argv=(
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                ),
            ),
        ),
        max_iterations=2,
    )
    run_id = loop.create(
        "divide",
        "Implement divide(a, b) with an explicit ZeroDivisionError('zero')",
        ("divide(6, 3) == 2", "divide(1, 0) raises ZeroDivisionError('zero')"),
        ("calc.py",),
    )
    return run_id, await loop.run(run_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated Codex experiment")
    parser.add_argument("--real-codex", action="store_true")
    args = parser.parse_args()
    if not args.real_codex:
        parser.error("--real-codex explicitly authorizes Codex usage")
    root = Path(tempfile.mkdtemp(prefix="aec-phase4-")) / "experiment"
    print(f"Evidence directory: {root}")
    run_id, status = asyncio.run(run_demo(root))
    print(f"run_id={run_id} status={status}")


if __name__ == "__main__":
    main()
