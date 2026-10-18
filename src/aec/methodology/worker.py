"""Deterministic observation and type finding; no governance authority."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from aec.communication.broker import Broker
from aec.communication.errors import ConflictError
from aec.communication.schemas import Message, MessageType
from aec.communication.schemas import MethodologyProposal as ACPMethodologyProposal
from aec.security.identities import Identity, Role

from .contracts import Change, MethodologyFinding, MethodologyProposal
from .governance import ProposalStore
from .observation import ObservationCollector


class MethodologyWorker:
    def __init__(self, collector: ObservationCollector, store: ProposalStore) -> None:
        self.collector = collector
        self.store = store

    def propose_type_gate(
        self,
        methodology_id: str,
        version: str,
        *,
        proposal_id: str,
        proposed_version: str,
        expected_metric: Literal[
            "average_review_iterations", "acceptance_rate"
        ] = "average_review_iterations",
    ) -> tuple[MethodologyFinding, MethodologyProposal]:
        observations = [
            o
            for o in self.collector.collect()
            if o.methodology_id == methodology_id and o.version == version
        ]
        recurring = [o.run_id for o in observations if "typing" in o.findings]
        if len(recurring) < 2:
            raise ValueError("at least two typed runs are needed for this finding")
        finding = MethodologyFinding(
            finding_id=f"finding:{proposal_id}",
            category="typing",
            observation=f"Typing findings occurred in {len(recurring)} runs.",
            hypothesis="A type gate before QA may reduce review iterations.",
            evidence_refs=tuple(recurring),
        )
        proposal = MethodologyProposal(
            proposal_id=proposal_id,
            methodology_id=methodology_id,
            base_version=version,
            proposed_version=proposed_version,
            problem=finding.observation,
            hypothesis=finding.hypothesis,
            evidence_refs=finding.evidence_refs,
            changes=(
                Change(
                    operation="add_quality_gate",
                    gate="type_check",
                    position="before_qa",
                ),
            ),
            expected_metric=expected_metric,
            expected_direction=(
                "increase" if expected_metric == "acceptance_rate" else "decrease"
            ),
            risks="The gate may delay reviews or reject valid work.",
            validation_criteria=(
                "Compare paired tasks with identical acceptance criteria."
            ),
            rollback_plan=f"Select {methodology_id}@{version} for new runs.",
        )
        self.store.submit(proposal)
        return finding, proposal

    def notify(
        self,
        proposal: MethodologyProposal,
        broker: Broker,
        worker: Identity,
        orchestrator: Identity,
    ) -> None:
        if worker.role != Role.METHODOLOGY or orchestrator.role != Role.ORCHESTRATOR:
            raise PermissionError("methodology worker and orchestrator required")
        stored, _, sha = self.store.get(proposal.proposal_id)
        if stored != proposal:
            raise ValueError("proposal differs from persisted content")
        message_id = f"methodology:{proposal.proposal_id}"
        existing = broker.get_message(message_id)
        if existing is not None:
            if (
                existing.metadata.get("proposal_sha256") != sha
                or existing.sender != worker.agent_id
                or existing.recipient != orchestrator.agent_id
                or existing.type != MessageType.METHODOLOGY_PROPOSAL
            ):
                raise ValueError("conflicting methodology notification")
            return
        try:
            broker.publish(
                Message(
                    protocol="ACP",
                    version="0.1",
                    message_id=message_id,
                    run_id=proposal.proposal_id,
                    task_id=proposal.methodology_id,
                    sender=worker.agent_id,
                    recipient=orchestrator.agent_id,
                    type=MessageType.METHODOLOGY_PROPOSAL,
                    iteration=0,
                    created_at=datetime.now(timezone.utc),
                    payload=ACPMethodologyProposal(
                        title=proposal.proposal_id, rationale=proposal.hypothesis
                    ),
                    correlation_id=proposal.proposal_id,
                    metadata={"proposal_sha256": sha},
                ),
                worker,
            )
        except ConflictError:
            existing = broker.get_message(message_id)
            if (
                existing is None
                or existing.metadata.get("proposal_sha256") != sha
                or existing.sender != worker.agent_id
                or existing.recipient != orchestrator.agent_id
                or existing.type != MessageType.METHODOLOGY_PROPOSAL
            ):
                raise
