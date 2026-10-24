"""Deterministic Red, Green and regression checks for trusted local tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from aec.engineering.git import capture, changed_files, commit_hash
from aec.engineering.models import QAReviewResult
from aec.execution.policy import ExecutionPolicy
from aec.persistence.database import Database


class TDDChecks:
    def __init__(
        self,
        workspace: Path,
        required_tests: tuple[Path, ...],
        command: tuple[str, ...],
        policy: ExecutionPolicy,
        db: Database,
        run_id: str,
        *,
        timeout_seconds: float = 120,
        allowed_paths: tuple[str, ...] | None = None,
    ) -> None:
        policy.require(workspace)
        if not required_tests or not command or not run_id or timeout_seconds <= 0:
            raise ValueError("required tests, command, run ID and timeout required")
        self.workspace = workspace.resolve(strict=True)
        self.required_tests = tuple(self._path(path) for path in required_tests)
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.db = db
        self.run_id = run_id
        self.allowed_paths = allowed_paths
        self.original = {
            path: self._digest(path) for path in self.required_tests if path.is_file()
        }

    def _test_hashes(self) -> dict[str, str]:
        return {
            str(path.relative_to(self.workspace)): self._digest(path)
            for path in self.required_tests
        }

    def _snapshot(self) -> dict[str, object]:
        return {
            "workspace": str(self.workspace),
            "tests": self._test_hashes(),
            "command": list(self.command),
            "timeout_seconds": self.timeout_seconds,
        }

    def _stored_snapshot(self) -> dict[str, object]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT content,sha256 FROM tdd_red_snapshots WHERE run_id=?",
                (self.run_id,),
            ).fetchone()
        if (
            row is None
            or hashlib.sha256(row["content"].encode()).hexdigest() != row["sha256"]
        ):
            raise ValueError("Red snapshot is missing or invalid")
        snapshot = json.loads(row["content"])
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "workspace",
            "tests",
            "command",
            "timeout_seconds",
        }:
            raise ValueError("Red snapshot has an invalid shape")
        return snapshot

    def _path(self, path: Path) -> Path:
        resolved = (self.workspace / path).resolve()
        if self.workspace not in resolved.parents or path.is_absolute():
            raise ValueError("required test is outside the workspace")
        return resolved

    @staticmethod
    def _digest(path: Path) -> str:
        if not path.is_file() or path.is_symlink():
            raise ValueError("required test is missing or is a symlink")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _run(self) -> bool:
        try:
            return (
                subprocess.run(
                    ("env", "PYTHONDONTWRITEBYTECODE=1", *self.command),
                    cwd=self.workspace,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout_seconds,
                ).returncode
                == 0
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("required tests timed out") from exc

    def red(self, _: dict[str, str]) -> bool:
        snapshot = self._snapshot()
        tests = self._test_hashes()
        if any(
            tests[str(path.relative_to(self.workspace))] != digest
            for path, digest in self.original.items()
        ):
            raise ValueError("preexisting required test changed")
        content = json.dumps(snapshot, sort_keys=True)
        digest = hashlib.sha256(content.encode()).hexdigest()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO tdd_red_snapshots VALUES (?,?,?)",
                (self.run_id, content, digest),
            )
        if self._stored_snapshot() != snapshot:
            raise ValueError("Red snapshot changed between attempts")
        return self._run()

    def green(self, _: dict[str, str]) -> bool:
        if self._snapshot() != self._stored_snapshot():
            raise ValueError("required tests changed after Red")
        return self._run()

    def regression(self, inputs: dict[str, str]) -> bool:
        if not self.green(inputs):
            return False
        if self.allowed_paths is None:
            return True
        with self.db.connect() as connection:
            previous = connection.execute(
                "SELECT content,sha256 FROM tdd_review_snapshots WHERE run_id=?",
                (self.run_id,),
            ).fetchone()
        if previous is None:
            commit, files = capture(self.workspace, self.allowed_paths)
            content = json.dumps(
                {"commit": commit, "files": files, "tests": self._test_hashes()},
                sort_keys=True,
            )
            digest = hashlib.sha256(content.encode()).hexdigest()
            with self.db.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO tdd_review_snapshots VALUES (?,?,?)",
                    (self.run_id, content, digest),
                )
        return self._review_snapshot_valid()

    def _review_snapshot_valid(self) -> bool:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT content,sha256 FROM tdd_review_snapshots WHERE run_id=?",
                (self.run_id,),
            ).fetchone()
        if (
            row is None
            or hashlib.sha256(row["content"].encode()).hexdigest() != row["sha256"]
        ):
            raise ValueError("TDD review snapshot is missing or invalid")
        snapshot = json.loads(row["content"])
        return (
            isinstance(snapshot, dict)
            and snapshot.get("commit") == commit_hash(self.workspace)
            and snapshot.get("tests") == self._test_hashes()
            and not changed_files(self.workspace)
        )

    def qa(self, inputs: dict[str, str]) -> bool:
        review = QAReviewResult.model_validate(json.loads(inputs["review"]))
        if self.allowed_paths is None:
            return review.review_status == "PASSED"
        return (
            review.review_status == "PASSED"
            and self._review_snapshot_valid()
            and review.reviewed_commit == commit_hash(self.workspace)
        )
