import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from aec.engineering.git import commit_hash
from aec.execution.policy import ExecutionPolicy
from aec.methodologies.tdd import TDDChecks
from aec.persistence.database import Database


def test_red_green_and_required_test_integrity(tmp_path: Path) -> None:
    test = tmp_path / "required.txt"
    test.write_text("acceptance")
    db = Database(tmp_path / "aec.sqlite")
    checks = TDDChecks(
        tmp_path,
        (Path("required.txt"),),
        (
            "python",
            "-c",
            "from pathlib import Path; assert Path('feature.txt').exists()",
        ),
        ExecutionPolicy((tmp_path,)),
        db,
        "run-1",
    )
    assert checks.red({}) is False
    (tmp_path / "feature.txt").write_text("implemented")
    restarted = TDDChecks(
        tmp_path,
        (Path("required.txt"),),
        checks.command,
        ExecutionPolicy((tmp_path,)),
        db,
        "run-1",
    )
    assert restarted.green({}) is True
    changed_command = TDDChecks(
        tmp_path,
        (Path("required.txt"),),
        ("python", "-c", "pass"),
        ExecutionPolicy((tmp_path,)),
        db,
        "run-1",
    )
    with pytest.raises(ValueError, match="changed after Red"):
        changed_command.green({})
    test.write_text("weakened")
    with pytest.raises(ValueError, match="changed after Red"):
        restarted.regression({})


def test_preexisting_test_change_blocks_red(tmp_path: Path) -> None:
    test = tmp_path / "required.txt"
    test.write_text("acceptance")
    db = Database(tmp_path / "aec.sqlite")
    checks = TDDChecks(
        tmp_path,
        (Path("required.txt"),),
        ("python", "-c", "raise SystemExit(1)"),
        ExecutionPolicy((tmp_path,)),
        db,
        "run-2",
    )
    test.write_text("changed")
    with pytest.raises(ValueError, match="preexisting"):
        checks.red({})


def test_review_snapshot_survives_restart_and_binds_qa_commit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    (project / "required.txt").write_text("required")
    (project / "solution.py").write_text("raise NotImplementedError\n")
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=AEC",
            "-c",
            "user.email=aec@localhost",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    db = Database(tmp_path / "aec.sqlite")
    policy = ExecutionPolicy((tmp_path,))
    command = ("python", "-c", "from solution import answer; assert answer() == 42")
    checks = TDDChecks(
        project,
        (Path("required.txt"),),
        command,
        policy,
        db,
        "review-run",
        allowed_paths=("solution.py",),
    )
    assert checks.red({}) is False
    (project / "solution.py").write_text("def answer(): return 42\n")
    assert checks.green({}) is True
    assert checks.regression({}) is True
    commit = commit_hash(project)
    review = {
        "task_id": "review-run",
        "iteration": 1,
        "reviewed_commit": commit,
        "review_status": "PASSED",
        "findings": [],
    }
    restarted = TDDChecks(
        project,
        (Path("required.txt"),),
        command,
        policy,
        db,
        "review-run",
        allowed_paths=("solution.py",),
    )
    assert restarted.regression({}) is True
    assert restarted.qa({"review": json.dumps(review)}) is True
    review["reviewed_commit"] = "a" * 40
    assert restarted.qa({"review": json.dumps(review)}) is False
    review["reviewed_commit"] = commit
    (project / "solution.py").write_text("def answer(): return 0\n")
    assert restarted.qa({"review": json.dumps(review)}) is False
    with db.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE tdd_review_snapshots SET content='changed' WHERE run_id=?",
                ("review-run",),
            )
