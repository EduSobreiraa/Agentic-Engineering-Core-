"""Run the implementer, QA, and orchestrator ACP scenario."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from aec.communication.broker import Broker
from aec.communication.schemas import ArtifactRef, Message, MessageType
from aec.security.identities import Identity, Role


@dataclass
class SimulatedAgent:
    broker: Broker
    identity: Identity

    def receive(self, expected: MessageType) -> Message:
        delivery = self.broker.claim(self.identity.agent_id, self.identity)
        assert delivery is not None and delivery.message.type == expected
        self.broker.ack(delivery, self.identity)
        return delivery.message

    def send(
        self,
        recipient: SimulatedAgent,
        kind: MessageType,
        payload: dict[str, object],
        *,
        iteration: int = 0,
        refs: list[ArtifactRef] | None = None,
    ) -> Message:
        message = Message.model_validate(
            {
                "protocol": "ACP",
                "version": "0.1",
                "message_id": str(uuid4()),
                "run_id": "demo-run",
                "task_id": "demo-task",
                "sender": self.identity.agent_id,
                "recipient": recipient.identity.agent_id,
                "type": kind,
                "iteration": iteration,
                "created_at": datetime.now(timezone.utc),
                "payload": payload,
                "artifact_refs": refs or [],
            }
        )
        return self.broker.publish(message, self.identity)


def run_demo(database_path: str | Path) -> list[Message]:
    broker = Broker(database_path)
    orchestrator = SimulatedAgent(
        broker, broker.register("orchestrator", Role.ORCHESTRATOR)
    )
    implementer = SimulatedAgent(
        broker, broker.register("implementer", Role.IMPLEMENTER)
    )
    qa = SimulatedAgent(broker, broker.register("qa-adversarial", Role.QA))
    gate = SimulatedAgent(broker, broker.register("quality-gate", Role.QUALITY_GATE))

    orchestrator.send(
        implementer,
        MessageType.TASK_ASSIGNED,
        {
            "title": "Implementar exemplo",
            "instructions": "Produzir artefato simulado",
        },
    )
    implementer.receive(MessageType.TASK_ASSIGNED)

    def submit(version: int) -> str:
        content = f"artefato simulado versão {version}".encode()
        artifact_id = f"example-v{version}"
        ref = ArtifactRef(
            artifact_id=artifact_id,
            kind="source",
            uri=f"artifact://demo-run/{artifact_id}",
            sha256=hashlib.sha256(content).hexdigest(),
            created_by="implementer",
        )
        implementer.send(
            orchestrator,
            MessageType.ARTIFACT_SUBMITTED,
            {
                "summary": f"Versão {version}",
                "artifact_id": artifact_id,
            },
            iteration=version - 1,
            refs=[ref],
        )
        orchestrator.receive(MessageType.ARTIFACT_SUBMITTED)
        return artifact_id

    first = submit(1)
    orchestrator.send(
        qa,
        MessageType.REVIEW_REQUEST,
        {
            "artifact_id": first,
            "criteria": "Revisar o resultado",
        },
    )
    qa.receive(MessageType.REVIEW_REQUEST)
    finding = qa.send(
        orchestrator,
        MessageType.FINDING_REPORTED,
        {
            "artifact_id": first,
            "severity": "medium",
            "description": "Falta ajuste",
        },
    )
    orchestrator.receive(MessageType.FINDING_REPORTED)
    orchestrator.send(
        implementer,
        MessageType.REWORK_REQUEST,
        {
            "finding_message_id": finding.message_id,
            "instructions": "Corrigir o ajuste",
        },
        iteration=1,
    )
    implementer.receive(MessageType.REWORK_REQUEST)
    second = submit(2)
    orchestrator.send(
        qa,
        MessageType.REVIEW_REQUEST,
        {
            "artifact_id": second,
            "criteria": "Reavaliar o ajuste",
        },
        iteration=1,
    )
    qa.receive(MessageType.REVIEW_REQUEST)
    qa.send(
        orchestrator,
        MessageType.VALIDATION_RESULT,
        {
            "artifact_id": second,
            "passed": True,
            "evidence": "Revisão simulada aprovada",
        },
        iteration=1,
    )
    orchestrator.receive(MessageType.VALIDATION_RESULT)
    validation = gate.send(
        orchestrator,
        MessageType.VALIDATION_RESULT,
        {
            "artifact_id": second,
            "passed": True,
            "evidence": "Quality gate simulado positivo",
        },
        iteration=1,
    )
    orchestrator.receive(MessageType.VALIDATION_RESULT)
    orchestrator.send(
        gate,
        MessageType.TASK_COMPLETED,
        {
            "summary": "Tarefa simulada concluída",
            "validation_message_id": validation.message_id,
        },
        iteration=1,
    )
    gate.receive(MessageType.TASK_COMPLETED)
    return broker.get_history("demo-run")


def main() -> None:
    parser = argparse.ArgumentParser(description="Executa a simulação ACP v0.1")
    parser.add_argument(
        "database", nargs="?", help="Arquivo SQLite; temporário se omitido"
    )
    args = parser.parse_args()
    if args.database:
        print(f"Mensagens persistidas: {len(run_demo(args.database))}")
    else:
        with TemporaryDirectory() as directory:
            history = run_demo(Path(directory) / "demo.sqlite")
            print(f"Mensagens persistidas: {len(history)}")


if __name__ == "__main__":
    main()
