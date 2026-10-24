"""Measured, repeatable comparison for trusted Arena candidates on Linux."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from aec.engineering.gates import CommandGate
from aec.engineering.git import changed_files, commit_hash
from aec.execution.policy import ExecutionPolicy


@dataclass(frozen=True)
class BenchmarkSample:
    elapsed_ns: int
    peak_rss_kib: int
    stdout_sha256: str

    def record(self) -> dict[str, int | str]:
        return asdict(self)


def measure(
    snapshot: Path, commit: str, command: CommandGate, policy: ExecutionPolicy
) -> BenchmarkSample:
    """Measure one direct child; refuse results from a changed snapshot."""
    if sys.platform != "linux" or not Path("/usr/bin/time").is_file():
        raise RuntimeError("Arena memory measurement requires Linux GNU time")
    policy.require(snapshot)
    if not command.argv or commit_hash(snapshot) != commit or changed_files(snapshot):
        raise ValueError("benchmark command or snapshot is invalid")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as metrics:
        started = time.perf_counter_ns()
        # shortcut: detached children escape; sandbox untrusted code.
        process = subprocess.Popen(
            (
                "/usr/bin/time",
                "-f",
                "%M",
                "-o",
                f"/proc/self/fd/{metrics.fileno()}",
                *command.argv,
            ),
            cwd=snapshot,
            stdout=output,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            start_new_session=True,
            pass_fds=(metrics.fileno(),),
        )
        try:
            process.wait(timeout=command.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise TimeoutError("Arena benchmark timed out") from exc
        elapsed = time.perf_counter_ns() - started
        if process.returncode != 0:
            raise RuntimeError(f"Arena benchmark exited {process.returncode}")
        metrics.seek(0)
        peak_rss_kib = int(metrics.read().strip())
        output.seek(0)
        digest = hashlib.file_digest(output, "sha256").hexdigest()
    if commit_hash(snapshot) != commit or changed_files(snapshot):
        raise RuntimeError("candidate snapshot changed during benchmark")
    return BenchmarkSample(elapsed, peak_rss_kib, digest)


def dominates(
    first: tuple[BenchmarkSample, ...], second: tuple[BenchmarkSample, ...]
) -> bool:
    """Choose only when every sample beats every opposing sample on both axes."""
    return bool(
        first
        and second
        and len({sample.stdout_sha256 for sample in (*first, *second)}) == 1
        and max(sample.elapsed_ns for sample in first)
        < min(sample.elapsed_ns for sample in second)
        and max(sample.peak_rss_kib for sample in first)
        < min(sample.peak_rss_kib for sample in second)
    )
