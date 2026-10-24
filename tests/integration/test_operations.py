import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from aec.operations.cli import main
from aec.operations.queries import OperationalReader
from aec.persistence.database import Database


def operational_database(path: Path) -> Path:
    Database(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO workflow_runs VALUES (?,?,?,?,?,?,?,?)",
            ("run-1", "{}", "a" * 64, "INTERRUPTED", "2026-01-01T00:00:00+00:00",
             None, None, "secret workflow error"),
        )
        db.execute(
            "INSERT INTO workflow_steps VALUES (?,?,?,?,?,?,?,?)",
            ("run-1", "agent", "INTERRUPTED", 1, "exec-1", None, None,
             '{"output":"secret output"}'),
        )
        db.execute(
            "INSERT INTO workflow_events(run_id,step_id,kind,detail,created_at) "
            "VALUES (?,?,?,?,?)",
            ("run-1", "agent", "INTERRUPTED", "secret detail",
             "2026-01-01T00:00:01+00:00"),
        )
        db.execute(
            "INSERT INTO agents VALUES (?,?,?)",
            ("agent-1", "IMPLEMENTER", "2026-01-01T00:00:00+00:00"),
        )
        db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
            ("message-1", "run-1", "agent", "agent-1", "agent-1",
             "TASK_ASSIGNED", json.dumps({"correlation_id": "corr-1",
                "payload": {"instructions": "secret prompt"}}),
             "2026-01-01T00:00:02+00:00"),
        )
        db.execute(
            "INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("exec-1", "run-1", "agent", "agent-1", "codex", "/tmp/work",
             "message-1", "b" * 64, "INTERRUPTED", "secret result",
             "2026-01-01T00:00:03+00:00", "2026-01-01T00:00:04+00:00"),
        )
        db.execute(
            "INSERT INTO workflow_approvals(run_id,step_id,status) VALUES (?,?,?)",
            ("run-1", "approval", "PENDING"),
        )
    return path


def test_operational_cli_reads_events_without_exposing_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = operational_database(tmp_path / "aec.sqlite")
    before = path.stat().st_mtime_ns
    assert main(["--database", str(path), "--json", "runs", "timeline", "run-1"]) == 0
    assert path.stat().st_mtime_ns == before
    output = capsys.readouterr().out
    timeline = json.loads(output)
    assert {event["source"] for event in timeline} == {
        "workflow_events", "messages", "executions"
    }
    assert timeline[0]["kind"] == "INTERRUPTED"
    execution = next(event for event in timeline if event["source"] == "executions")
    assert execution["kind"] == "CURRENT_STATUS_RECORDED"
    assert execution["at"] == "2026-01-01T00:00:04+00:00"
    assert next(event for event in timeline if event["source"] == "messages")[
        "correlation_id"
    ] == "corr-1"
    assert "secret" not in output
    assert main(["--database", str(path), "--json", "approvals", "pending"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "workflows": [
            {"run_id": "run-1", "status": "PENDING", "step_id": "approval"}
        ],
        "methodologies": [],
    }
    assert main([
        "--database", str(path), "--json", "processes", "inspect", "run-1"
    ]) == 0
    processes = json.loads(capsys.readouterr().out)
    assert processes[0]["observed_state"] == "UNKNOWN"
    assert processes[0]["quarantined"] is True
    assert processes[0]["reconciliation_pending"] is True


def test_operational_reader_preserves_unknown_states_and_missing_database(
    tmp_path: Path,
) -> None:
    path = operational_database(tmp_path / "aec.sqlite")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE workflow_runs SET status='FUTURE' WHERE run_id='run-1'")
        db.execute(
            "UPDATE messages SET content='broken JSON' WHERE message_id='message-1'"
        )
    reader = OperationalReader(path)
    assert reader.runs()[0]["status_known"] is False
    message = next(
        event for event in reader.timeline("run-1") if event["source"] == "messages"
    )
    assert message["correlation_id"] is None
    assert not (tmp_path / "missing.sqlite").exists()
    with pytest.raises(FileNotFoundError):
        OperationalReader(tmp_path / "missing.sqlite")
    assert not (tmp_path / "missing.sqlite").exists()


def test_timeline_shows_policy_and_model_outcome_without_private_content(
    tmp_path: Path,
) -> None:
    path = operational_database(tmp_path / "aec.sqlite")
    content = json.dumps({
        "version": "2", "max_invocations_per_run": 2,
        "max_concurrent_agents": 1, "max_retries": 0,
        "execution_timeout_seconds": 60, "max_duration_seconds": 300,
        "max_iterations": 2, "secret": "never expose",
    })
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO run_execution_budgets VALUES (?,?,?)",
            ("run-1", content, hashlib.sha256(content.encode()).hexdigest()),
        )
        db.execute(
            "INSERT INTO execution_settings VALUES (?,?,?,?)",
            ("exec-1", "gpt-test", "high", None),
        )
    timeline = OperationalReader(path).timeline("run-1")
    policy = next(event for event in timeline if event["kind"] == "POLICY_SNAPSHOT")
    execution = next(event for event in timeline if event["source"] == "executions")
    assert policy["max_retries"] == 0
    assert execution["requested_model"] == "gpt-test"
    assert execution["effective_model"] is None
    assert "never expose" not in json.dumps(timeline)


@pytest.mark.parametrize(
    ("command", "expected_type"),
    [
        (["status"], dict),
        (["runs", "list"], list),
        (["runs", "inspect", "run-1"], dict),
        (["agents", "list"], list),
        (["metrics", "summary"], dict),
        (["methodologies", "list"], list),
    ],
)
def test_operational_commands_return_redacted_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
    expected_type: type[object],
) -> None:
    path = operational_database(tmp_path / "aec.sqlite")
    assert main(["--database", str(path), "--json", *command]) == 0
    output = capsys.readouterr().out
    assert isinstance(json.loads(output), expected_type)
    assert "secret" not in output
