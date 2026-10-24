"""Deterministic checks for a trusted, local direct-eval audit fixture."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

from aec.engineering.git import capture, changed_files, commit_hash
from aec.execution.policy import ExecutionPolicy
from aec.persistence.database import Database


def audit_snapshot(db: Database, run_id: str) -> tuple[dict[str, str], bytes, str]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT manifest,sha256,content FROM audit_artifact_snapshots "
            "WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise ValueError("audit artifact snapshot is missing")
    manifest_json = str(row["manifest"])
    content = bytes(row["content"])
    digest = hashlib.sha256(manifest_json.encode()).hexdigest()
    if digest != row["sha256"]:
        raise ValueError("audit manifest hash mismatch")
    manifest = json.loads(manifest_json)
    keys = {
        "run_id", "methodology_id", "version", "methodology_sha256",
        "definition_hash", "base_commit", "commit", "path", "content_sha256",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != keys
        or not all(isinstance(value, str) for value in manifest.values())
        or manifest["run_id"] != run_id
        or hashlib.sha256(content).hexdigest() != manifest["content_sha256"]
    ):
        raise ValueError("audit artifact snapshot is invalid")
    return manifest, content, digest


class StaticEvalAuditChecks:
    def __init__(self, workspace: Path, target: Path, policy: ExecutionPolicy) -> None:
        policy.require(workspace)
        self.workspace = workspace.resolve(strict=True)
        candidate = self.workspace / target
        if candidate.is_symlink():
            raise ValueError("audit target must be a regular file")
        self.target = candidate.resolve(strict=True)
        if target.is_absolute() or self.workspace not in self.target.parents:
            raise ValueError("audit target is outside the workspace")
        if not self.target.is_file():
            raise ValueError("audit target must be a regular file")
        self.relative_target = self.target.relative_to(self.workspace).as_posix()
        self.base_commit = commit_hash(self.workspace)
        self.policy = policy

    def scope(self, _: dict[str, str]) -> bool:
        self.policy.require(self.workspace)
        return self.target.is_file() and not self.target.is_symlink()

    def static(self, _: dict[str, str]) -> bool:
        return (
            self.scope({})
            and commit_hash(self.workspace) == self.base_commit
            and not changed_files(self.workspace)
            and self._calls("eval")
        )

    def retest(self, _: dict[str, str]) -> bool:
        return (
            self.scope({})
            and commit_hash(self.workspace) == self.base_commit
            and changed_files(self.workspace) == [self.relative_target]
            and not self._calls("eval")
            and self._imports_ast()
            and self._calls("literal_eval", module="ast")
        )

    def _imports_ast(self) -> bool:
        try:
            tree = ast.parse(self.target.read_text())
        except (OSError, SyntaxError):
            return False
        return any(
            isinstance(node, ast.Import)
            and any(
                alias.name == "ast" and alias.asname is None
                for alias in node.names
            )
            for node in ast.walk(tree)
        )

    def _calls(self, name: str, *, module: str | None = None) -> bool:
        try:
            tree = ast.parse(self.target.read_text())
        except (OSError, SyntaxError):
            return False
        return any(
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id == name
                if module is None
                else isinstance(node.func, ast.Attribute)
                and node.func.attr == name
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == module
            )
            for node in ast.walk(tree)
        )


class StaticEvalIntegrityChecks(StaticEvalAuditChecks):
    """Retest and approve exact committed bytes of a trusted local fixture."""

    def __init__(
        self, workspace: Path, target: Path, policy: ExecutionPolicy,
        db: Database, run_id: str,
    ) -> None:
        super().__init__(workspace, target, policy)
        self.db = db
        self.run_id = run_id

    def _binding(self) -> tuple[str, str, str, str]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT b.methodology_id,b.version,b.sha256,r.definition_hash "
                "FROM methodology_run_bindings b JOIN workflow_runs r "
                "ON r.run_id=b.run_id WHERE b.run_id=?",
                (self.run_id,),
            ).fetchone()
        if (
            row is None
            or row["methodology_id"] != "security-audit"
            or row["version"] != "1.0.3"
        ):
            raise ValueError("audit run is not bound to the integrity methodology")
        return (
            str(row["methodology_id"]), str(row["version"]),
            str(row["sha256"]), str(row["definition_hash"]),
        )

    @staticmethod
    def _safe_bytes(content: bytes) -> bool:
        try:
            tree = ast.parse(content.decode("utf-8"))
        except (UnicodeDecodeError, SyntaxError):
            return False
        has_import = any(
            isinstance(node, ast.Import)
            and any(
                alias.name == "ast" and alias.asname is None
                for alias in node.names
            )
            for node in ast.walk(tree)
        )
        direct_eval = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "eval"
            for node in ast.walk(tree)
        )
        literal_eval = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "literal_eval"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ast"
            for node in ast.walk(tree)
        )
        return has_import and not direct_eval and literal_eval

    def _committed_bytes(self, commit: str) -> bytes:
        try:
            result = subprocess.run(
                [
                    "git", "-C", str(self.workspace), "cat-file", "blob",
                    f"{commit}:{self.relative_target}",
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
        except subprocess.SubprocessError as exc:
            raise ValueError("committed audit artifact is unavailable") from exc
        return result.stdout

    def retest(self, _: dict[str, str]) -> bool:
        with self.db.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM audit_artifact_snapshots WHERE run_id=?", (self.run_id,)
            ).fetchone()
        if existing is not None:
            return bool(self.integrity(self.run_id))
        if (
            not self.scope({})
            or commit_hash(self.workspace) != self.base_commit
            or changed_files(self.workspace) != [self.relative_target]
        ):
            return False
        commit, files = capture(self.workspace, (self.relative_target,))
        if files != [self.relative_target]:
            return False
        content = self._committed_bytes(commit)
        if len(content) > 1_000_000 or not self._safe_bytes(content):
            return False
        methodology_id, version, methodology_sha, definition_hash = self._binding()
        manifest = {
            "run_id": self.run_id,
            "methodology_id": methodology_id,
            "version": version,
            "methodology_sha256": methodology_sha,
            "definition_hash": definition_hash,
            "base_commit": self.base_commit,
            "commit": commit,
            "path": self.relative_target,
            "content_sha256": hashlib.sha256(content).hexdigest(),
        }
        serialized = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO audit_artifact_snapshots VALUES (?,?,?,?)",
                (self.run_id, serialized, digest, content),
            )
        return self.integrity(self.run_id) == digest

    def integrity(self, run_id: str) -> str:
        if run_id != self.run_id or not self.scope({}):
            raise ValueError("audit scope does not match the snapshot")
        manifest, content, digest = audit_snapshot(self.db, run_id)
        binding_keys = (
            "methodology_id", "version", "methodology_sha256", "definition_hash"
        )
        if manifest["path"] != self.relative_target or tuple(
            manifest[key] for key in binding_keys
        ) != self._binding():
            raise ValueError("audit snapshot binding changed")
        if (
            self._committed_bytes(manifest["commit"]) != content
            or commit_hash(self.workspace, f"{manifest['commit']}^")
            != manifest["base_commit"]
            or commit_hash(self.workspace) != manifest["commit"]
            or changed_files(self.workspace)
            or self.target.read_bytes() != content
        ):
            raise ValueError("audit workspace differs from the validated snapshot")
        return digest
