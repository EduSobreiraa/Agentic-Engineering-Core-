import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aec.engineering.gates import run_gates
from aec.engineering.git import capture, commit_hash
from aec.engineering.models import (
    ImplementationResult,
    QAReviewResult,
    ReviewFinding,
    ReworkRequest,
)
from aec.execution.policy import ExecutionPolicy


def test_review_contract_rejects_unproved_failure() -> None:
    data = {
        "task_id": "task",
        "iteration": 1,
        "reviewed_commit": "a" * 40,
        "review_status": "FAILED",
        "findings": [],
    }
    with pytest.raises(ValidationError):
        QAReviewResult.model_validate_json(json.dumps(data))
    data["findings"] = [
        {
            "finding_id": "f1",
            "severity": "HIGH",
            "category": "correctness",
            "title": "Broken",
            "description": "Reproduced failure",
            "reproduction_steps": ["run failing case"],
            "status": "REPRODUCED",
        }
    ]
    review = QAReviewResult.model_validate_json(json.dumps(data))
    assert review.review_status == "FAILED"
    data["review_status"] = "PASSED"
    with pytest.raises(ValidationError):
        QAReviewResult.model_validate_json(json.dumps(data))


def test_finding_requires_evidence_classification() -> None:
    with pytest.raises(ValidationError):
        ReviewFinding.model_validate(
            {
                "finding_id": "f1",
                "severity": "HIGH",
                "category": "bug",
                "title": "x",
                "description": "x",
            }
        )


def test_implementation_and_rework_reject_invalid_references() -> None:
    with pytest.raises(ValidationError):
        ImplementationResult.model_validate(
            {
                "task_id": "task",
                "iteration": 1,
                "status": "SUCCEEDED",
                "base_commit": "not-a-commit",
                "result_commit": "a" * 40,
                "changed_files": ["calc.py"],
                "implementation_summary": "done",
            }
        )
    with pytest.raises(ValidationError):
        ReworkRequest.model_validate(
            {
                "task_id": "task",
                "iteration": 1,
                "target_agent": "implementer",
                "base_commit": "a" * 40,
                "findings_to_address": [],
                "required_changes": ["fix"],
                "acceptance_criteria": ["works"],
                "max_remaining_iterations": 1,
            }
        )


def test_capture_rejects_out_of_scope_change(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "allowed.py").write_text("pass\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@localhost",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    assert len(commit_hash(tmp_path)) == 40
    (tmp_path / "other.py").write_text("pass\n")
    with pytest.raises(PermissionError):
        capture(tmp_path, ("allowed.py",))


def test_untrusted_snapshot_cannot_run_gates(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    untrusted = tmp_path / "untrusted"
    trusted.mkdir()
    untrusted.mkdir()
    with pytest.raises(PermissionError, match="not trusted"):
        run_gates(
            untrusted,
            "a" * 40,
            (),
            policy=ExecutionPolicy((trusted,)),
        )
