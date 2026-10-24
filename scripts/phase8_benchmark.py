"""Run the existing paired scripted benchmark and retain its evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

from aec.methodology.benchmark import LocalBenchmarkRunner
from aec.methodology.contracts import BenchmarkRun
from aec.methodology.governance import candidate
from tests.integration.test_methodology_benchmark import proposal
from tests.scenarios.test_methodology_evolution import base_version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    baseline = base_version()
    proposed = candidate(baseline, proposal())
    runs = asyncio.run(LocalBenchmarkRunner(
        directory, repetitions=args.repetitions
    ).compare(baseline, proposed))
    pairs: dict[tuple[str, int], dict[str, BenchmarkRun]] = {}
    for run in runs:
        pairs.setdefault((run.task_id, run.repetition), {})[run.arm] = run
    equivalent = all(
        set(pair) == {"baseline", "candidate"}
        and pair["baseline"].acceptance_sha256
        == pair["candidate"].acceptance_sha256
        for pair in pairs.values()
    )
    failures = sum(run.failure is not None for run in runs)
    outcomes = {
        arm: {
            "accepted": sum(run.accepted for run in runs if run.arm == arm),
            "total": sum(run.arm == arm for run in runs),
            "invocations": sum(
                run.agent_invocations for run in runs if run.arm == arm
            ),
        }
        for arm in ("baseline", "candidate")
    }
    report = {
        "classification": "INCONCLUSIVE",
        "reason": (
            "Scripted agents and two reference tasks do not establish "
            "real Codex quality or productivity."
        ),
        "paired_acceptance_equal": equivalent,
        "failures": failures,
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
            text=True, timeout=5,
        ).stdout.strip(),
        "repetitions": args.repetitions,
        "baseline_version": baseline.version,
        "candidate_version": proposed.version,
        "outcomes": outcomes,
        "runs": [
            {
                **run.model_dump(mode="json"),
                "database_sha256": hashlib.sha256(
                    Path(run.database_path).read_bytes()
                ).hexdigest(),
            }
            for run in runs
        ],
    }
    (directory / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({
        "classification": report["classification"],
        "failures": failures,
        "paired_acceptance_equal": equivalent,
        "outcomes": outcomes,
        "report": str(directory / "report.json"),
    }, indent=2))
    if not equivalent or failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
