"""A real subscription-consuming test, disabled by default."""

import asyncio
import os
from pathlib import Path

import pytest

from aec.communication.schemas import MessageType
from aec.execution.demo import run_demo


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
