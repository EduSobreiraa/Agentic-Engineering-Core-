"""Optional local Orca CLI discovery without forwarding session data."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class OrcaCliAdapter:
    def __init__(self, executable: str = "orca-ide") -> None:
        self.executable = executable

    def current_worktree(self, expected_path: Path) -> dict[str, str]:
        executable = shutil.which(self.executable)
        if executable is None:
            raise FileNotFoundError("Orca CLI is unavailable")
        completed = subprocess.run(
            [executable, "worktree", "current", "--json"],
            cwd=expected_path, capture_output=True, text=True,
            check=True, timeout=10,
        )
        try:
            payload: Any = json.loads(completed.stdout)
            worktree = payload["result"]["worktree"]
            if payload["ok"] is not True or worktree["hostId"] != "local":
                raise ValueError("Orca returned a nonlocal worktree")
            if Path(worktree["path"]).resolve() != expected_path.resolve():
                raise ValueError("Orca returned another worktree")
            return {
                "id": str(worktree["id"]),
                "path": str(worktree["path"]),
                "branch": str(worktree["branch"]),
                "workspace_status": str(worktree["workspaceStatus"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Orca worktree response is invalid") from exc
