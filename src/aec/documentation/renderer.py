"""Render evidence without interpreting agent assertions as verified results."""

from __future__ import annotations

import re
from importlib.resources import files
from string import Template

from aec.communication.schemas import ArtifactRef

from .collector import ExecutionEvidence


def safe(value: object) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)(?:OPENAI_API_KEY|API_KEY|TOKEN|PASSWORD)\s*[:=]\s*\S+",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    text = text.replace("\\", "\\\\")
    for char in "`*_[]<>|#!":
        text = text.replace(char, "\\" + char)
    return text.replace("\n", " ").replace("\r", " ")


def render(name: str, fields: dict[str, str]) -> str:
    template = (
        files("aec.documentation")
        .joinpath("templates", f"{name}.md")
        .read_text(encoding="utf-8")
    )
    return Template(template).substitute(fields)


def methodology_document(kind: str, fields: list[tuple[str, str]]) -> str:
    """Render a persisted methodology record without changing its authority."""
    return f"# {safe(kind)}\n\n" + "\n".join(
        f"## {safe(label)}\n\n{safe(value)}\n" for label, value in fields
    )


def execution_report(
    evidence: ExecutionEvidence, document_id: str = "indisponível"
) -> str:
    iterations: list[str] = []
    trace: list[str] = []
    findings_detail: list[str] = []
    gate_rows: list[str] = []
    all_agents = sorted(
        {
            agent
            for message in evidence.messages
            for agent in (message.sender, message.recipient)
        }
    )
    for item in evidence.iterations:
        implementation = item.implementation
        review = item.review
        gates = (
            ", ".join(f"{safe(g.gate_id)}={g.status}" for g in item.gates)
            or "não executados"
        )
        for gate in item.gates:
            gate_rows.append(
                f"| {item.number} | {safe(gate.gate_id)} | {gate.status} | "
                f"{safe(gate.validator)} | {safe(gate.evidence)} |"
            )
        if review:
            for finding in review.findings:
                correction = next(
                    (
                        later.implementation.result_commit
                        for later in evidence.iterations
                        if later.number > item.number and later.implementation
                    ),
                    "indisponível",
                )
                findings_detail.append(
                    f"- {safe(finding.finding_id)} "
                    f"({finding.severity}, {finding.status}): "
                    f"{safe(finding.description)}. "
                    f"Implementação subsequente: {safe(correction)}."
                )
        commit = (
            safe(implementation.result_commit) if implementation else "indisponível"
        )
        iterations.append(
            f"| {item.number} | {safe(item.status)} | "
            f"{commit} | "
            f"{safe(review.review_status) if review else 'indisponível'} | {gates} |"
        )
        if implementation:
            approval = (
                "APPROVED"
                if evidence.status == "APPROVED"
                and implementation.result_commit == evidence.head_commit
                and any(
                    status == "APPROVED" and actor
                    for _, status, actor in evidence.approvals
                )
                else "indisponível"
            )
            for path in implementation.changed_files:
                file_findings = (
                    ", ".join(
                        safe(finding.finding_id)
                        for finding in review.findings
                        if finding.file == path
                    )
                    if review
                    else ""
                ) or "indisponível"
                trace.append(
                    f"| {safe(evidence.task_id)} | {item.number} | {commit} | "
                    f"{safe(path)} | {file_findings} | {gates} | "
                    f"{approval} | {safe(document_id)} |"
                )
    approvals = (
        ", ".join(
            f"{safe(status)} por {safe(actor) if actor else 'indisponível'}"
            for _, status, actor in evidence.approvals
        )
        or "nenhuma registrada"
    )
    refs = sorted(
        ref.uri + " (sha256 " + ref.sha256 + ")" for ref in evidence.source_refs
    )
    return render(
        "execution-report",
        {
            "run_id": safe(evidence.run_id),
            "task_id": safe(evidence.task_id),
            "specification": safe(evidence.specification),
            "criteria": "\n".join(f"- {safe(c)}" for c in evidence.criteria),
            "status": safe(evidence.status),
            "base_commit": safe(evidence.base_commit),
            "head_commit": safe(evidence.head_commit),
            "methodologies": ", ".join(safe(v) for v in evidence.methodologies)
            or (
                "dev-adversarial@1.0.0 "
                "(constante de engenharia; definição não recuperada)"
            ),
            "document_id": safe(document_id),
            "agents": ", ".join(safe(agent) for agent in all_agents) or "indisponível",
            "executions": "\n".join(
                f"| {safe(item.execution_id)} | {safe(item.agent_id)} | "
                f"{safe(item.status)} | "
                f"{item.exit_code if item.exit_code is not None else 'indisponível'} |"
                for item in evidence.executions
            )
            or "| indisponível | | | |",
            "state_history": "\n".join(
                f"| {safe(item.run_id)} | {safe(item.step_id or 'workflow')} | "
                f"{safe(item.kind)} | {safe(item.created_at)} |"
                for item in evidence.workflow_events
            )
            or "| indisponível | | | |",
            "findings": "\n".join(findings_detail) or "- nenhum finding registrado",
            "gate_rows": "\n".join(gate_rows)
            or "| indisponível | | não executado | | |",
            "iterations": "\n".join(iterations) or "| indisponível | | | | |",
            "trace": "\n".join(trace) or "| indisponível | | | | | | | |",
            "approvals": approvals,
            "adrs": "\n".join(
                f"- {safe(adr_id)} revisão {revision}: {safe(status)}, sha256 {digest}"
                for adr_id, revision, status, digest in evidence.adrs
            )
            or "- nenhuma registrada",
            "artifacts": "\n".join(f"- {safe(ref)}" for ref in refs)
            or "- nenhum registrado",
            "warnings": "\n".join(f"- {safe(w)}" for w in evidence.warnings)
            or "- nenhuma",
        },
    )


def qa_summary(evidence: ExecutionEvidence) -> str:
    lines = [
        f"- Iteração {item.number}: {safe(item.review.review_status)}; "
        + ", ".join(safe(f.finding_id + " " + f.status) for f in item.review.findings)
        for item in evidence.iterations
        if item.review
    ]
    return render(
        "qa-summary",
        {
            "run_id": safe(evidence.run_id),
            "findings": "\n".join(lines) or "- sem revisão registrada",
        },
    )


def change_summary(evidence: ExecutionEvidence) -> str:
    lines = [
        f"- Iteração {item.number}, commit {safe(item.implementation.result_commit)}: "
        + ", ".join(safe(path) for path in item.implementation.changed_files)
        for item in evidence.iterations
        if item.implementation
    ]
    return render(
        "change-summary",
        {
            "run_id": safe(evidence.run_id),
            "changes": "\n".join(lines) or "- sem implementação registrada",
        },
    )


def technical_documentation(
    title: str, body: str, source_refs: list[ArtifactRef], scope: str
) -> str:
    if not title or not body or not scope or not source_refs:
        raise ValueError("technical proposal requires title, body, scope and sources")
    return render(
        "technical-documentation",
        {
            "title": safe(title),
            "body": safe(body),
            "scope": safe(scope),
            "sources": "\n".join(
                f"- {safe(ref.uri)} sha256 {ref.sha256}" for ref in source_refs
            ),
        },
    )
