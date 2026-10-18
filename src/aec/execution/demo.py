"""Run two independent Codex turns through ACP."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from aec.communication.broker import Broker
from aec.communication.schemas import Message, MessageType, TaskAssigned
from aec.security.identities import Identity, Role

from .adapters.codex_cli import CodexCliAdapter
from .manager import ExecutionManager
from .models import ExecutionRequest, ExecutionStatus


def _send(
    broker: Broker,
    sender: Identity,
    recipient: Identity,
    kind: MessageType,
    payload: dict[str, str],
    run_id: str,
    task_id: str,
) -> Message:
    return broker.publish(
        Message.model_validate(
            {
                "protocol": "ACP",
                "version": "0.1",
                "message_id": str(uuid4()),
                "run_id": run_id,
                "task_id": task_id,
                "sender": sender.agent_id,
                "recipient": recipient.agent_id,
                "type": kind,
                "iteration": 0,
                "created_at": datetime.now(timezone.utc),
                "payload": payload,
            }
        ),
        sender,
    )


async def run_demo(
    database: Path, root: Path, adapter: CodexCliAdapter | None = None
) -> list[Message]:
    root.mkdir(parents=True, exist_ok=True)
    a_dir = root / "implementer"
    b_dir = root / "qa"
    a_dir.mkdir(exist_ok=True)
    b_dir.mkdir(exist_ok=True)
    broker = Broker(database)
    orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
    implementer = broker.register("implementer", Role.IMPLEMENTER)
    qa = broker.register("qa", Role.QA)
    manager = ExecutionManager(broker, {"codex": adapter or CodexCliAdapter()}, [root])
    run_id, task_id = str(uuid4()), str(uuid4())
    task = _send(
        broker,
        orchestrator,
        implementer,
        MessageType.TASK_ASSIGNED,
        {
            "title": "Texto curto",
            "instructions": (
                "Escreva uma proposta textual de função que soma dois inteiros. "
                "Não altere arquivos."
            ),
        },
        run_id,
        task_id,
    )
    claimed = broker.claim(implementer.agent_id, implementer)
    assert claimed is not None and claimed.message.message_id == task.message_id
    assert isinstance(task.payload, TaskAssigned)
    first = await manager.execute(
        ExecutionRequest(
            execution_id=str(uuid4()),
            run_id=run_id,
            task_id=task_id,
            agent_id=implementer.agent_id,
            executor="codex",
            prompt=task.payload.instructions,
            working_directory=a_dir,
            sandbox_mode="read-only",
        ),
        implementer,
        task.message_id,
    )
    broker.ack(claimed, implementer)
    if first.status != ExecutionStatus.SUCCEEDED:
        raise RuntimeError(f"implementer execution ended: {first.status}")
    submission = broker.claim(orchestrator.agent_id, orchestrator)
    assert (
        submission is not None
        and submission.message.type == MessageType.ARTIFACT_SUBMITTED
    )
    broker.ack(submission, orchestrator)
    ref = first.artifact_refs[0]
    review = _send(
        broker,
        orchestrator,
        qa,
        MessageType.REVIEW_REQUEST,
        {
            "artifact_id": ref.artifact_id,
            "criteria": "Revise criticamente a proposta textual.",
        },
        run_id,
        task_id,
    )
    claimed_review = broker.claim(qa.agent_id, qa)
    assert (
        claimed_review is not None
        and claimed_review.message.message_id == review.message_id
    )
    second = await manager.execute(
        ExecutionRequest(
            execution_id=str(uuid4()),
            run_id=run_id,
            task_id=task_id,
            agent_id=qa.agent_id,
            executor="codex",
            prompt=(
                "Revise criticamente esta proposta e descreva um achado concreto:\n"
                + first.output
            ),
            working_directory=b_dir,
            sandbox_mode="read-only",
        ),
        qa,
        review.message_id,
    )
    broker.ack(claimed_review, qa)
    if second.status != ExecutionStatus.SUCCEEDED:
        raise RuntimeError(f"QA execution ended: {second.status}")
    finding = broker.claim(orchestrator.agent_id, orchestrator)
    assert finding is not None and finding.message.type == MessageType.FINDING_REPORTED
    broker.ack(finding, orchestrator)
    return broker.get_history(run_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Two Codex agents over ACP")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.database and args.workspace:
        history = asyncio.run(run_demo(args.database, args.workspace))
    elif not args.database and not args.workspace:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            history = asyncio.run(run_demo(base / "aec.sqlite", base / "workspace"))
    else:
        parser.error("--database and --workspace must be given together")
    print(f"Run {history[0].run_id}: {len(history)} ACP messages")
    for message in history:
        print(message.type.value, message.sender, "->", message.recipient)


if __name__ == "__main__":
    main()
