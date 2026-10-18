from __future__ import annotations

import subprocess
from pathlib import Path


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def commit_hash(path: Path, ref: str = "HEAD") -> str:
    commit = git(path, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if len(commit) not in {40, 64} or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("invalid Git commit")
    return commit


def create_worktree(repository: Path, target: Path, commit: str) -> None:
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    git(
        repository,
        "worktree",
        "add",
        "--detach",
        str(target),
        commit_hash(repository, commit),
    )


def changed_files(path: Path) -> list[str]:
    tracked = git(path, "diff", "--name-only", "HEAD").splitlines()
    untracked = git(path, "ls-files", "--others", "--exclude-standard").splitlines()
    ignored = git(
        path, "ls-files", "--others", "--ignored", "--exclude-standard"
    ).splitlines()
    return sorted(set(tracked + untracked + ignored))


def capture(path: Path, allowed_paths: tuple[str, ...]) -> tuple[str, list[str]]:
    changes = changed_files(path)
    if not changes:
        raise ValueError("implementation produced no changed files")
    if any(
        not any(
            file == prefix or file.startswith(prefix.rstrip("/") + "/")
            for prefix in allowed_paths
        )
        or not (path / file).is_file()
        or (path / file).is_symlink()
        for file in changes
    ):
        raise PermissionError("change outside allowed paths or symlink")
    git(path, "add", "--", *changes)
    git(
        path,
        "-c",
        "user.name=AEC",
        "-c",
        "user.email=aec@localhost",
        "commit",
        "-m",
        "AEC implementation snapshot",
    )
    return commit_hash(path), changes
