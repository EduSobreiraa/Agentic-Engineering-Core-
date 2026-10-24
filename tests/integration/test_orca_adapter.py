import json
import subprocess
from pathlib import Path

import pytest

from aec.operations.orca import OrcaCliAdapter


def test_orca_adapter_whitelists_fields_and_rejects_other_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aec.operations.orca.shutil.which", lambda name: "/bin/orca-ide"
    )
    seen: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        payload = {
            "ok": True,
            "result": {"worktree": {
                "id": "repo::worktree", "path": str(tmp_path),
                "branch": "refs/heads/feature", "hostId": "local",
                "workspaceStatus": "in-progress",
                "terminalPreview": "secret session content",
            }},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("aec.operations.orca.subprocess.run", run)
    result = OrcaCliAdapter().current_worktree(tmp_path)
    assert result == {
        "id": "repo::worktree", "path": str(tmp_path),
        "branch": "refs/heads/feature", "workspace_status": "in-progress",
    }
    assert "secret" not in json.dumps(result)
    assert seen == [["/bin/orca-ide", "worktree", "current", "--json"]]

    def remote(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "ok": True, "result": {"worktree": {
                "id": "x", "path": str(tmp_path), "branch": "main",
                "hostId": "remote", "workspaceStatus": "in-progress",
            }}
        }), "")

    monkeypatch.setattr("aec.operations.orca.subprocess.run", remote)
    with pytest.raises(ValueError, match="invalid"):
        OrcaCliAdapter().current_worktree(tmp_path)


def test_orca_adapter_reports_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aec.operations.orca.shutil.which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="unavailable"):
        OrcaCliAdapter().current_worktree(tmp_path)
