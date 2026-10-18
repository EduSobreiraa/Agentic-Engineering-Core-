"""Authorized ACP-to-executor bridge."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aec.communication.broker import Broker
from aec.communication.schemas import (
    ArtifactRef,
    ArtifactSubmitted,
    FindingReported,
    Message,
    MessageType,
    ReviewRequest,
    TaskAssigned,
)
from aec.security.identities import Identity, Role

from .base import ExecutionAdapter
from .models import AgentSession, ExecutionRequest, ExecutionResult, ExecutionStatus
from .repository import ExecutionRepository


class ExecutionManager:
    def __init__(
        self,
        broker: Broker,
        adapters: dict[str, ExecutionAdapter],
        allowed_roots: list[Path],
        *,
        max_concurrency: int = 2,
        writable_agents: frozenset[str] = frozenset(),
    ) -> None:
        if not allowed_roots or max_concurrency < 1:
            raise ValueError("allowed_roots and positive concurrency are required")
        self.broker = broker
        self.adapters = adapters
        self.allowed_roots = [root.resolve(strict=True) for root in allowed_roots]
        self.writable_agents = writable_agents
        forbidden = {
            Path("/"),
            Path("/etc"),
            Path("/usr"),
            Path("/var"),
            Path.home(),
            Path.home() / ".codex",
        }
        if any(root in forbidden for root in self.allowed_roots):
            raise ValueError("sensitive directory cannot be an execution root")
        self.repository = ExecutionRepository(broker.db)
        self.repository.reconcile()
        self._slots = asyncio.Semaphore(max_concurrency)
        self._workspaces: dict[Path, asyncio.Lock] = {}

    def _authorize(
        self, request: ExecutionRequest, identity: Identity, source_id: str
    ) -> Message:
        self.broker._identity(identity)
        source = self.broker.get_message(source_id)
        if (
            source is None
            or source.recipient != identity.agent_id
            or request.agent_id != identity.agent_id
        ):
            raise PermissionError("execution requires an assigned ACP message")
        if source.run_id != request.run_id or source.task_id != request.task_id:
            raise PermissionError("execution does not match ACP task")
        expected = (
            MessageType.TASK_ASSIGNED
            if identity.role == Role.IMPLEMENTER
            else MessageType.REVIEW_REQUEST
        )
        if identity.role not in (Role.IMPLEMENTER, Role.QA) or source.type != expected:
            raise PermissionError("role cannot execute this ACP message")
        if (
            isinstance(source.payload, TaskAssigned)
            and request.prompt != source.payload.instructions
        ):
            raise PermissionError("prompt does not match ACP assignment")
        if request.sandbox_mode == "workspace-write" and (
            identity.agent_id not in self.writable_agents
            or source.metadata.get("workspace_write") is not True
        ):
            raise PermissionError("agent is not authorized for workspace-write")
        if not any(
            request.working_directory == root
            or root in request.working_directory.parents
            for root in self.allowed_roots
        ):
            raise PermissionError("working directory is outside allowed roots")
        return source

    async def execute(
        self,
        request: ExecutionRequest,
        identity: Identity,
        source_message_id: str,
    ) -> ExecutionResult:
        source = self._authorize(request, identity, source_message_id)
        adapter = self.adapters.get(request.executor)
        if adapter is None:
            raise ValueError("executor is not registered")
        provider_session_id = None
        session = None
        if request.session_id:
            session = self.repository.get_session(request.session_id)
            if (
                session is None
                or session.agent_id != identity.agent_id
                or session.executor != request.executor
                or session.working_directory != request.working_directory
            ):
                raise PermissionError(
                    "session does not belong to this agent and workspace"
                )
            provider_session_id = session.provider_session_id
        if not self.repository.create(request, source_message_id):
            previous = self.repository.get(request.execution_id)
            if isinstance(previous, ExecutionResult):
                if previous.status == ExecutionStatus.SUCCEEDED:
                    self._publish(source, identity, previous)
                return previous
            raise RuntimeError(
                "execution already exists and cannot be repeated automatically"
            )
        workspace_lock = self._workspaces.setdefault(
            request.working_directory, asyncio.Lock()
        )
        async with self._slots, workspace_lock:
            self.repository.status(request.execution_id, ExecutionStatus.STARTING)
            try:
                if not await adapter.healthcheck():
                    raise RuntimeError("executor unavailable or not authenticated")
                self.repository.status(request.execution_id, ExecutionStatus.RUNNING)
                result = await adapter.execute(request, provider_session_id)
                if (
                    result.execution_id != request.execution_id
                    or result.agent_id != request.agent_id
                ):
                    raise ValueError("adapter returned another execution")
            except asyncio.CancelledError:
                now = datetime.now(timezone.utc)
                self.repository.finish(
                    ExecutionResult(
                        execution_id=request.execution_id,
                        agent_id=request.agent_id,
                        status=ExecutionStatus.CANCELLED,
                        started_at=now,
                        finished_at=now,
                        error="cancelled",
                    )
                )
                raise
            except Exception as exc:
                now = datetime.now(timezone.utc)
                result = ExecutionResult(
                    execution_id=request.execution_id,
                    agent_id=request.agent_id,
                    status=ExecutionStatus.FAILED,
                    started_at=now,
                    finished_at=now,
                    error=type(exc).__name__,
                )
            if result.session_id:
                now = datetime.now(timezone.utc)
                internal_id = session.session_id if session else str(uuid4())
                self.repository.save_session(
                    AgentSession(
                        session_id=internal_id,
                        provider_session_id=result.session_id,
                        executor=request.executor,
                        agent_id=request.agent_id,
                        working_directory=request.working_directory,
                        state=result.status,
                        created_at=session.created_at if session else now,
                        last_used_at=now,
                    )
                )
                result.session_id = internal_id
            if result.status == ExecutionStatus.SUCCEEDED and isinstance(
                source.payload, ReviewRequest
            ):
                try:
                    self._finding(source.payload, result.output)
                except ValueError:
                    result.status = ExecutionStatus.FAILED
                    result.error = "invalid_qa_output"
            if result.status == ExecutionStatus.SUCCEEDED:
                try:
                    result.artifact_refs = [self._store_output(request, result.output)]
                except OSError:
                    result.status = ExecutionStatus.FAILED
                    result.error = "artifact_storage_failed"
            self.repository.finish(result)
            if result.status == ExecutionStatus.SUCCEEDED:
                self._publish(source, identity, result)
            return result

    def _store_output(self, request: ExecutionRequest, output: str) -> ArtifactRef:
        artifact_id = hashlib.sha256(request.execution_id.encode()).hexdigest()
        directory = (
            Path(self.broker.db.path).resolve().parent
            / "artifacts"
            / hashlib.sha256(request.run_id.encode()).hexdigest()
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / artifact_id
        path.write_text(output)
        return ArtifactRef(
            artifact_id=artifact_id,
            kind="text",
            uri=f"artifact://{request.run_id}/{artifact_id}",
            sha256=hashlib.sha256(output.encode()).hexdigest(),
            created_by=request.agent_id,
        )

    def _publish(
        self, source: Message, identity: Identity, result: ExecutionResult
    ) -> None:
        ref = result.artifact_refs[0]
        payload: FindingReported | ArtifactSubmitted
        if isinstance(source.payload, ReviewRequest):
            kind = MessageType.FINDING_REPORTED
            payload = self._finding(source.payload, result.output)
        else:
            kind = MessageType.ARTIFACT_SUBMITTED
            payload = ArtifactSubmitted(
                summary=result.output[:4000], artifact_id=ref.artifact_id
            )
        message = Message(
            protocol="ACP",
            version="0.1",
            message_id=f"execution:{result.execution_id}",
            run_id=source.run_id,
            task_id=source.task_id,
            sender=identity.agent_id,
            recipient=source.sender,
            type=kind,
            iteration=source.iteration,
            created_at=result.finished_at,
            payload=payload,
            causation_id=source.message_id,
            artifact_refs=[ref],
            metadata={"execution_id": result.execution_id},
        )
        self.broker.publish(message, identity)

    @staticmethod
    def _finding(review: ReviewRequest, output: str) -> FindingReported:
        data = json.loads(output)
        if not isinstance(data, dict) or set(data) != {"severity", "description"}:
            raise ValueError("QA must return severity and description")
        finding = FindingReported.model_validate(
            {**data, "artifact_id": review.artifact_id}
        )
        return finding.model_copy(update={"description": finding.description[:4000]})

    async def cancel(self, execution_id: str) -> bool:
        for adapter in self.adapters.values():
            if await adapter.cancel(execution_id):
                return True
        return False
