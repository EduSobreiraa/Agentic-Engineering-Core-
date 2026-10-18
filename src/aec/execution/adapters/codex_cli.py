"""Codex CLI 0.163 JSONL adapter."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
from datetime import datetime, timezone

from aec.execution.base import ExecutionAdapter
from aec.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus


class CodexCliAdapter(ExecutionAdapter):
    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("codex")
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    async def healthcheck(self) -> bool:
        if self.executable is None:
            return False
        try:
            version = await asyncio.create_subprocess_exec(
                self.executable,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(version.communicate(), 5)
            match = re.search(rb"codex-cli 0\.(\d+)", out)
            if version.returncode != 0 or match is None:
                return False
            minor = int(match.group(1))
            if minor < 163:
                return False
            login = await asyncio.create_subprocess_exec(
                self.executable,
                "login",
                "status",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(login.wait(), 5)
            return login.returncode == 0
        except (OSError, asyncio.TimeoutError):
            return False

    @staticmethod
    def parse_events(stdout: str) -> tuple[str, str | None, dict[str, int | float]]:
        output = ""
        provider_session_id = None
        usage: dict[str, int | float] = {}
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("invalid Codex JSONL") from exc
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError("invalid Codex event")
            if event["type"] == "thread.started":
                provider_session_id = event.get("thread_id")
            elif event["type"] == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    output = str(item.get("text", ""))
            elif event["type"] == "turn.completed":
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    usage = {
                        k: v
                        for k, v in raw_usage.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)
                    }
        return output, provider_session_id, usage

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def cancel(self, execution_id: str) -> bool:
        process = self._running.get(execution_id)
        if process is None:
            return False
        self._cancelled.add(execution_id)
        await self._stop(process)
        return True

    async def execute(
        self, request: ExecutionRequest, provider_session_id: str | None = None
    ) -> ExecutionResult:
        started = datetime.now(timezone.utc)
        if self.executable is None:
            raise FileNotFoundError("Codex CLI is not installed")
        args = [
            self.executable,
            "exec",
            "--json",
            "--sandbox",
            request.sandbox_mode,
            "--skip-git-repo-check",
        ]
        if provider_session_id:
            args.extend(["resume", provider_session_id, "-"])
        else:
            args.append("-")
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=request.working_directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._running[request.execution_id] = process
        try:
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(request.prompt.encode()),
                    request.timeout_seconds,
                )
                status = (
                    ExecutionStatus.SUCCEEDED
                    if process.returncode == 0
                    else ExecutionStatus.FAILED
                )
            except asyncio.TimeoutError:
                await self._stop(process)
                stdout_bytes, stderr_bytes = b"", b""
                status = ExecutionStatus.TIMED_OUT
            if request.execution_id in self._cancelled:
                status = ExecutionStatus.CANCELLED
            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")
            output = ""
            session_id = provider_session_id
            usage: dict[str, int | float] = {}
            if stdout:
                try:
                    output, parsed_session, usage = self.parse_events(stdout)
                    session_id = parsed_session or session_id
                except ValueError:
                    if status == ExecutionStatus.SUCCEEDED:
                        status = ExecutionStatus.FAILED
            if status == ExecutionStatus.SUCCEEDED and not output:
                status = ExecutionStatus.FAILED
            error = (
                None if status == ExecutionStatus.SUCCEEDED else status.value.lower()
            )
            return ExecutionResult(
                execution_id=request.execution_id,
                agent_id=request.agent_id,
                status=status,
                session_id=session_id,
                output=output,
                exit_code=process.returncode,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                error=error,
                usage=usage,
                stdout=stdout,
                stderr=stderr,
            )
        except asyncio.CancelledError:
            await self._stop(process)
            raise
        finally:
            self._running.pop(request.execution_id, None)
            self._cancelled.discard(request.execution_id)
