from pathlib import Path

from aec.communication.schemas import MessageType, TaskCompleted
from aec.simulation.agents import run_demo


def test_complete_exchange_is_traceable(tmp_path: Path) -> None:
    history = run_demo(tmp_path / "demo.sqlite")
    assert [message.type for message in history] == [
        MessageType.TASK_ASSIGNED,
        MessageType.ARTIFACT_SUBMITTED,
        MessageType.REVIEW_REQUEST,
        MessageType.FINDING_REPORTED,
        MessageType.REWORK_REQUEST,
        MessageType.ARTIFACT_SUBMITTED,
        MessageType.REVIEW_REQUEST,
        MessageType.VALIDATION_RESULT,
        MessageType.VALIDATION_RESULT,
        MessageType.TASK_COMPLETED,
    ]
    assert isinstance(history[-1].payload, TaskCompleted)
    assert history[-1].payload.validation_message_id == history[-2].message_id
