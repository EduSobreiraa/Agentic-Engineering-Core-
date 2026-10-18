"""Render immutable methodology records as Markdown."""

from __future__ import annotations

from pathlib import Path

from aec.methodology.contracts import (
    MethodologyApproval,
    MethodologyEvaluation,
    MethodologyProposal,
    MethodologyPublication,
)


class DocumentationWorker:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def rfc(self, proposal: MethodologyProposal) -> Path:
        text = (
            f"# {proposal.proposal_id}\n\n"
            f"Methodology: {proposal.methodology_id}@{proposal.base_version} → "
            f"{proposal.proposed_version}\n\n"
            f"## Problem\n\n{proposal.problem}\n\n"
            f"## Hypothesis\n\n{proposal.hypothesis}\n\n"
            f"## Evidence\n\n"
            + "\n".join(f"- {ref}" for ref in proposal.evidence_refs)
            + "\n\n## Change\n\n"
            + "\n".join(
                f"- {change.operation}: {change.gate} {change.position}"
                for change in proposal.changes
            )
            + f"\n\n## Expected effect\n\n{proposal.expected_metric}: "
            f"{proposal.expected_direction}\n\n"
            f"## Validation\n\n{proposal.validation_criteria}\n\n"
            f"## Risks\n\n{proposal.risks}\n\n"
            f"## Reversal\n\n{proposal.rollback_plan}\n\n"
            "Human approval is required.\n"
        )
        return self._write(f"{proposal.proposal_id}.md", text)

    def evaluation(self, result: MethodologyEvaluation) -> Path:
        text = (
            f"# {result.evaluation_id}\n\nVerdict: {result.verdict}\n\n"
            "## Checks\n\n"
            + "\n".join(f"- {item}" for item in result.checks)
            + "\n\n## Compared tasks\n\n"
            + "\n".join(
                f"- {pair.task_id}: {pair.baseline} to {pair.candidate}; "
                f"criteria {pair.acceptance_sha256}; conditions {pair.conditions}"
                for pair in result.pairs
            )
            + "\n\n## Limitations\n\n"
            + "\n".join(f"- {item}" for item in result.limitations)
            + "\n"
        )
        return self._write(f"{result.evaluation_id.replace(':', '-')}.md", text)

    def decision(self, approval: MethodologyApproval) -> Path:
        text = (
            f"# Decision for {approval.proposal_id}\n\n"
            f"Decision: {approval.decision}\n\nActor: {approval.actor}\n\n"
            f"Date: {approval.decided_at.isoformat()}\n\n"
            f"Proposal SHA-256: {approval.proposal_sha256}\n"
        )
        return self._write(f"{approval.proposal_id}-decision.md", text)

    def publication(self, result: MethodologyPublication) -> Path:
        text = (
            f"# {result.methodology_id}@{result.version}\n\n"
            f"Proposal: {result.proposal_id}\n\n"
            f"Approved by: {result.approved_by}\n\n"
            f"Published at: {result.published_at.isoformat()}\n\n"
            f"Artifact SHA-256: {result.sha256}\n"
        )
        return self._write(f"{result.proposal_id}-publication.md", text)

    def _write(self, name: str, content: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / name
        with path.open("x", encoding="utf-8") as file:
            file.write(content)
        return path
