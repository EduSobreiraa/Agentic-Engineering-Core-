"""Persistent lifecycle and publication guarded by local human authorization."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Literal

from aec.persistence.database import Database
from aec.workflows.loader import definition_hash, validate
from aec.workflows.models import WorkflowDefinition, WorkflowStep

from .contracts import (
    BenchmarkCase,
    BenchmarkPair,
    EvaluationVerdict,
    MethodologyApproval,
    MethodologyEvaluation,
    MethodologyProposal,
    MethodologyPublication,
    MethodologyVersion,
    ProposalStatus,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _event(
    db: sqlite3.Connection,
    proposal_id: str,
    kind: str,
    actor: str | None = None,
    detail: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO methodology_events(proposal_id,kind,actor,detail,created_at) "
        "VALUES (?,?,?,?,?)",
        (proposal_id, kind, actor, detail, now().isoformat()),
    )


class MethodologyRegistry:
    def __init__(
        self, db: Database, *, validators: frozenset[str] = frozenset()
    ) -> None:
        self.db = db
        self.validators = validators

    def _validate_dependencies(self, version: MethodologyVersion) -> None:
        validate(version.definition)
        missing = {
            step.validator
            for step in version.definition.steps
            if step.type == "validation"
            and step.validator is not None
            and step.validator not in self.validators
        }
        if missing:
            raise ValueError(f"unregistered validators: {sorted(missing)}")

    def seed(self, version: MethodologyVersion, *, actor: str) -> None:
        """Install an initial trusted version; never replace an existing version."""
        self._validate_dependencies(version)
        content = version.model_dump_json()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO methodology_versions VALUES (?,?,?,?,?,?,NULL)",
                (
                    version.methodology_id,
                    version.version,
                    content,
                    digest(content),
                    now().isoformat(),
                    actor,
                ),
            )
            db.execute(
                "INSERT INTO methodology_active VALUES (?,?)",
                (version.methodology_id, version.version),
            )

    def get(self, methodology_id: str, version: str) -> MethodologyVersion:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT definition,sha256 FROM methodology_versions "
                "WHERE methodology_id=? AND version=?",
                (methodology_id, version),
            ).fetchone()
        if row is None:
            raise KeyError((methodology_id, version))
        if digest(row["definition"]) != row["sha256"]:
            raise RuntimeError("published methodology hash mismatch")
        return MethodologyVersion.model_validate_json(row["definition"])

    def versions(self, methodology_id: str) -> list[str]:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT version FROM methodology_versions WHERE methodology_id=? "
                "ORDER BY published_at",
                (methodology_id,),
            ).fetchall()
        return [str(row["version"]) for row in rows]

    def list_methodologies(self) -> list[str]:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT methodology_id FROM methodology_active ORDER BY methodology_id"
            ).fetchall()
        return [str(row["methodology_id"]) for row in rows]

    def active(self, methodology_id: str) -> MethodologyVersion:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT version FROM methodology_active WHERE methodology_id=?",
                (methodology_id,),
            ).fetchone()
        if row is None:
            raise KeyError(methodology_id)
        return self.get(methodology_id, row["version"])

    def bind_run(self, run_id: str, methodology_id: str, version: str) -> None:
        published = self.get(methodology_id, version)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT definition,definition_hash FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            definition = WorkflowDefinition.model_validate_json(row["definition"])
            if definition_hash(definition) != row["definition_hash"]:
                raise RuntimeError("workflow definition hash mismatch")
            if definition_hash(definition) != definition_hash(published.definition):
                raise ValueError(
                    "workflow definition differs from selected methodology"
                )
            record = db.execute(
                "SELECT sha256 FROM methodology_versions WHERE methodology_id=? "
                "AND version=?",
                (methodology_id, version),
            ).fetchone()
            assert record is not None
            db.execute(
                "INSERT INTO methodology_run_bindings VALUES (?,?,?,?)",
                (run_id, methodology_id, version, record["sha256"]),
            )


class ProposalStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def submit(self, proposal: MethodologyProposal) -> None:
        self.draft(proposal)
        self.submit_draft(proposal.proposal_id)

    def draft(self, proposal: MethodologyProposal) -> None:
        content = proposal.model_dump_json()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for reference in proposal.evidence_refs:
                if (
                    db.execute(
                        "SELECT 1 FROM engineering_runs WHERE run_id=?", (reference,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError(f"unknown evidence: {reference}")
            if (
                db.execute(
                    "SELECT 1 FROM methodology_versions WHERE methodology_id=? "
                    "AND version=?",
                    (proposal.methodology_id, proposal.base_version),
                ).fetchone()
                is None
            ):
                raise ValueError("base version does not exist")
            db.execute(
                "INSERT INTO methodology_proposals(proposal_id,content,sha256,status) "
                "VALUES (?,?,?,?)",
                (
                    proposal.proposal_id,
                    content,
                    digest(content),
                    ProposalStatus.DRAFT,
                ),
            )
            _event(db, proposal.proposal_id, "DRAFT", detail=digest(content))

    def submit_draft(self, proposal_id: str) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT content,sha256,status FROM methodology_proposals "
                "WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if row is None or row["status"] != ProposalStatus.DRAFT:
                raise ValueError("proposal is not a draft")
            if digest(row["content"]) != row["sha256"]:
                raise RuntimeError("draft hash mismatch")
            db.execute(
                "UPDATE methodology_proposals SET status=? WHERE proposal_id=?",
                (ProposalStatus.SUBMITTED, proposal_id),
            )
            _event(db, proposal_id, "SUBMITTED", detail=row["sha256"])

    def get(self, proposal_id: str) -> tuple[MethodologyProposal, ProposalStatus, str]:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT content,sha256,status FROM methodology_proposals "
                "WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        if digest(row["content"]) != row["sha256"]:
            raise RuntimeError("proposal content hash mismatch")
        return (
            MethodologyProposal.model_validate_json(row["content"]),
            ProposalStatus(row["status"]),
            str(row["sha256"]),
        )

    def history(self, proposal_id: str) -> list[dict[str, str | int | None]]:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT * FROM methodology_events WHERE proposal_id=? "
                "ORDER BY event_id",
                (proposal_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def candidate(
    base: MethodologyVersion, proposal: MethodologyProposal
) -> MethodologyVersion:
    if (base.methodology_id, base.version) != (
        proposal.methodology_id,
        proposal.base_version,
    ):
        raise ValueError("proposal base does not match")
    steps = list(base.definition.steps)
    for change in proposal.changes:
        if change.gate in base.mandatory_gates or any(
            s.id == change.gate for s in steps
        ):
            raise ValueError("gate already exists")
        qa_steps = [step for step in steps if step.agent == "qa"]
        if not qa_steps:
            raise ValueError("QA step does not exist")
        for qa in qa_steps:
            steps[steps.index(qa)] = qa.model_copy(
                update={"depends_on": [*qa.depends_on, change.gate]}
            )
        steps.append(
            WorkflowStep(
                id=change.gate,
                type="validation",
                validator=change.gate,
                depends_on=sorted(
                    set().union(*(set(qa.depends_on) for qa in qa_steps))
                ),
            )
        )
    definition = base.definition.model_copy(
        update={
            "version": proposal.proposed_version,
            "steps": steps,
        }
    )
    validate(definition)
    return MethodologyVersion(
        methodology_id=base.methodology_id,
        version=proposal.proposed_version,
        definition=definition,
        mandatory_gates=base.mandatory_gates,
    )


class MethodologyEvaluator:
    def __init__(self, store: ProposalStore, registry: MethodologyRegistry) -> None:
        self.store = store
        self.registry = registry

    def evaluate(
        self,
        proposal_id: str,
        *,
        cases: tuple[BenchmarkCase, ...] = (),
        benchmark: Callable[[WorkflowDefinition, BenchmarkCase], float] | None = None,
    ) -> MethodologyEvaluation:
        proposal, status, sha = self.store.get(proposal_id)
        if status not in {ProposalStatus.SUBMITTED, ProposalStatus.UNDER_EVALUATION}:
            raise ValueError("proposal cannot be evaluated in this state")
        if status == ProposalStatus.SUBMITTED:
            with self.store.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE methodology_proposals SET status=? WHERE proposal_id=?",
                    (ProposalStatus.UNDER_EVALUATION, proposal_id),
                )
                _event(db, proposal_id, "UNDER_EVALUATION")
        measured: tuple[BenchmarkPair, ...] = ()
        try:
            base = self.registry.get(proposal.methodology_id, proposal.base_version)
            proposed = candidate(base, proposal)
            if proposal.proposed_version in self.registry.versions(
                proposal.methodology_id
            ):
                raise ValueError("proposed version is already published")
            checks: tuple[str, ...] = ("schema and governance passed",)
            limitations: tuple[str, ...]
            if not cases or benchmark is None:
                verdict = EvaluationVerdict.INCONCLUSIVE
                limitations = ("No controlled benchmark was executed.",)
            else:
                if len({case.task_id for case in cases}) != len(cases):
                    raise ValueError("duplicate benchmark task")
                measured = tuple(
                    BenchmarkPair(
                        **case.model_dump(),
                        baseline=benchmark(base.definition, case),
                        candidate=benchmark(proposed.definition, case),
                    )
                    for case in cases
                )
                before = sum(pair.baseline for pair in measured) / len(measured)
                after = sum(pair.candidate for pair in measured) / len(measured)
                improved = (
                    after < before
                    if proposal.expected_direction == "decrease"
                    else after > before
                )
                verdict = EvaluationVerdict.PASS if improved else EvaluationVerdict.FAIL
                checks += (f"paired mean: {before} -> {after}",)
                limitations = (
                    "Fixture comparison alone does not establish causality.",
                )
        except Exception as exc:
            verdict = EvaluationVerdict.FAIL
            checks = (f"{type(exc).__name__}: {exc}",)
            limitations = ()
        result = MethodologyEvaluation(
            evaluation_id=f"eval:{proposal_id}",
            proposal_id=proposal_id,
            proposal_sha256=sha,
            verdict=verdict,
            checks=checks,
            limitations=limitations,
            pairs=measured,
            baseline=sum(pair.baseline for pair in measured) / len(measured)
            if measured
            else None,
            candidate=sum(pair.candidate for pair in measured) / len(measured)
            if measured
            else None,
        )
        with self.store.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE methodology_proposals SET status=?,evaluation=? "
                "WHERE proposal_id=? AND status=?",
                (
                    ProposalStatus.EVALUATION_FAILED
                    if verdict == EvaluationVerdict.FAIL
                    else ProposalStatus.AWAITING_APPROVAL,
                    result.model_dump_json(),
                    proposal_id,
                    ProposalStatus.UNDER_EVALUATION,
                ),
            )
            _event(db, proposal_id, verdict.value, detail=result.model_dump_json())
        return result


class MethodologyGovernance:
    def __init__(
        self,
        store: ProposalStore,
        registry: MethodologyRegistry,
        *,
        local_approvers: frozenset[str],
    ) -> None:
        self.store = store
        self.registry = registry
        self.local_approvers = local_approvers

    def decide(
        self,
        proposal_id: str,
        actor: str,
        decision: Literal["APPROVED", "REJECTED", "CHANGES_REQUESTED"],
    ) -> MethodologyApproval:
        if actor not in self.local_approvers:
            raise PermissionError("local approver is not authorized")
        if decision not in {"APPROVED", "REJECTED", "CHANGES_REQUESTED"}:
            raise ValueError("invalid decision")
        proposal, status, sha = self.store.get(proposal_id)
        if status != ProposalStatus.AWAITING_APPROVAL:
            raise ValueError("proposal is not awaiting approval")
        decided_at = now()
        with self.store.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            evaluation = db.execute(
                "SELECT evaluation FROM methodology_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()["evaluation"]
            verdict = MethodologyEvaluation.model_validate_json(evaluation).verdict
            if decision == "APPROVED" and verdict not in {
                EvaluationVerdict.PASS,
                EvaluationVerdict.PASS_WITH_WARNINGS,
            }:
                raise ValueError("evaluation does not authorize approval")
            result = db.execute(
                "UPDATE methodology_proposals SET status=?,approval_by=?,approval_at=? "
                "WHERE proposal_id=? AND status=? AND sha256=?",
                (
                    decision,
                    actor,
                    decided_at.isoformat(),
                    proposal_id,
                    ProposalStatus.AWAITING_APPROVAL,
                    sha,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("proposal changed or already decided")
            _event(db, proposal_id, decision, actor, sha)
        return MethodologyApproval(
            proposal_id=proposal.proposal_id,
            proposal_sha256=sha,
            actor=actor,
            decided_at=decided_at,
            decision=decision,
        )

    def publish(self, proposal_id: str, actor: str) -> MethodologyPublication:
        if actor not in self.local_approvers:
            raise PermissionError("local publisher is not authorized")
        proposal, _, sha = self.store.get(proposal_id)
        with self.store.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,sha256,approval_by,approval_at,evaluation "
                "FROM methodology_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if row["status"] == ProposalStatus.PUBLISHED:
                published = db.execute(
                    "SELECT sha256,published_at,approved_by FROM methodology_versions "
                    "WHERE proposal_id=?",
                    (proposal_id,),
                ).fetchone()
                if published is None:
                    raise RuntimeError("published state without version")
                return MethodologyPublication(
                    proposal_id=proposal_id,
                    methodology_id=proposal.methodology_id,
                    version=proposal.proposed_version,
                    sha256=published["sha256"],
                    approved_by=published["approved_by"],
                    published_at=datetime.fromisoformat(published["published_at"]),
                )
            if (
                row["status"] != ProposalStatus.APPROVED
                or row["sha256"] != sha
                or not row["approval_by"]
                or not row["approval_at"]
            ):
                raise PermissionError("current proposal has no valid approval")
            evaluation = MethodologyEvaluation.model_validate_json(row["evaluation"])
            if evaluation.proposal_sha256 != sha or evaluation.verdict not in {
                EvaluationVerdict.PASS,
                EvaluationVerdict.PASS_WITH_WARNINGS,
            }:
                raise PermissionError("evaluation does not permit publication")
            active = db.execute(
                "SELECT version FROM methodology_active WHERE methodology_id=?",
                (proposal.methodology_id,),
            ).fetchone()
            if active is None or active["version"] != proposal.base_version:
                raise ValueError("base version conflict")
            base = self.registry.get(proposal.methodology_id, proposal.base_version)
            version = candidate(base, proposal)
            self.registry._validate_dependencies(version)
            content = version.model_dump_json()
            published_at = now()
            db.execute(
                "INSERT INTO methodology_versions VALUES (?,?,?,?,?,?,?)",
                (
                    proposal.methodology_id,
                    proposal.proposed_version,
                    content,
                    digest(content),
                    published_at.isoformat(),
                    row["approval_by"],
                    proposal_id,
                ),
            )
            db.execute(
                "UPDATE methodology_active SET version=? WHERE methodology_id=?",
                (proposal.proposed_version, proposal.methodology_id),
            )
            db.execute(
                "UPDATE methodology_proposals SET status=? WHERE proposal_id=?",
                (ProposalStatus.PUBLISHED, proposal_id),
            )
            _event(db, proposal_id, "PUBLISHED", actor, digest(content))
        return MethodologyPublication(
            proposal_id=proposal_id,
            methodology_id=proposal.methodology_id,
            version=proposal.proposed_version,
            sha256=digest(content),
            approved_by=row["approval_by"],
            published_at=published_at,
        )
