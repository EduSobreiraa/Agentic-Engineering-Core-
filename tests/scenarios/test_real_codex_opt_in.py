"""A real subscription-consuming test, disabled by default."""

import asyncio
import os
from pathlib import Path

import pytest

from aec.communication.schemas import MessageType
from aec.execution.adapters.codex_cli import CodexCliAdapter
from aec.execution.demo import run_demo
from aec.execution.models import ExecutionRequest, ExecutionStatus
from aec.execution.policy import ExecutionPolicy


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="set AEC_RUN_REAL_CODEX=1 to consume two real Codex turns",
)
def test_real_two_agent_exchange(tmp_path: Path) -> None:
    history = asyncio.run(run_demo(tmp_path / "aec.sqlite", tmp_path / "work"))
    assert [message.type for message in history] == [
        MessageType.TASK_ASSIGNED,
        MessageType.ARTIFACT_SUBMITTED,
        MessageType.REVIEW_REQUEST,
        MessageType.FINDING_REPORTED,
    ]


@pytest.mark.skipif(
    os.environ.get("AEC_RUN_REAL_CODEX") != "1",
    reason="set AEC_RUN_REAL_CODEX=1 to consume two real Codex turns",
)
def test_real_session_resume_in_non_git_directory(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(policy=ExecutionPolicy((tmp_path,)))

    async def run() -> None:
        first_request = ExecutionRequest(
            execution_id="resume-first",
            run_id="resume-check",
            task_id="read-only",
            agent_id="implementer",
            executor="codex",
            prompt="Reply with one short sentence about software testing.",
            working_directory=tmp_path,
            sandbox_mode="read-only",
        )
        first = await adapter.execute(first_request)
        assert first.status == ExecutionStatus.SUCCEEDED
        assert first.session_id
        second = await adapter.execute(
            first_request.model_copy(
                update={
                    "execution_id": "resume-second",
                    "prompt": "Reply with one short sentence about code review.",
                }
            ),
            first.session_id,
        )
        assert second.status == ExecutionStatus.SUCCEEDED
        assert second.session_id == first.session_id
        assert second.output

    asyncio.run(run())
