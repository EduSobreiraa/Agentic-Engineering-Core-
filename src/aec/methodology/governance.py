"""Persistent lifecycle and publication guarded by local human authorization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal
from urllib.parse import quote

from aec.persistence.database import Database
from aec.workflows.loader import definition_hash, validate
from aec.workflows.models import Condition, WorkflowDefinition, WorkflowStep

from .contracts import (
    BenchmarkCase,
    BenchmarkPair,
    BenchmarkRun,
    ChangeCategory,
    EvaluationVerdict,
    MethodologyApproval,
    MethodologyEvaluation,
    MethodologyProposal,
    MethodologyPublication,
    MethodologyVersion,
    ProposalStatus,
    RevisionVerification,
    SemanticChange,
    methodology_digest,
)

if TYPE_CHECKING:
    from .benchmark import LocalBenchmarkRunner


def now() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


BLOCKED_METHODOLOGY_VERSIONS = frozenset({("security-audit", "1.0.2")})


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
        if (version.methodology_id, version.version) in BLOCKED_METHODOLOGY_VERSIONS:
            raise PermissionError("methodology version is blocked")
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
        if (methodology_id, version) in BLOCKED_METHODOLOGY_VERSIONS:
            raise PermissionError("methodology version is blocked")
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

    def register_external_evidence(
        self,
        evidence_id: str,
        source_database: Path,
        source_run_id: str,
        workspace: Path,
        candidate_version: MethodologyVersion,
    ) -> str:
        """Register a checked source run by reference, without copying its rows."""
        source = source_database.resolve(strict=True)
        project = workspace.resolve(strict=True)
        candidate_sha = methodology_digest(candidate_version)
        evidence_sha = _source_evidence_digest(
            source, source_run_id, project, candidate_version
        )
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO revision_external_evidence VALUES (?,?,?,?,?,?,?)",
                (
                    evidence_id, str(source), source_run_id, str(project),
                    candidate_sha, evidence_sha, now().isoformat(),
                ),
            )
        return evidence_sha

    def submit(self, proposal: MethodologyProposal) -> None:
        self.draft(proposal)
        self.submit_draft(proposal.proposal_id)

    def draft(self, proposal: MethodologyProposal) -> None:
        content = proposal.model_dump_json()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for reference in proposal.evidence_refs:
                engineering = db.execute(
                    "SELECT 1 FROM engineering_runs WHERE run_id=?", (reference,)
                ).fetchone()
                workflow = db.execute(
                    "SELECT 1 FROM workflow_runs WHERE run_id=?", (reference,)
                ).fetchone()
                external = db.execute(
                    "SELECT 1 FROM revision_external_evidence WHERE evidence_id=?",
                    (reference,),
                ).fetchone()
                if engineering is None and workflow is None and external is None:
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
    if (base.methodology_id, base.version) in BLOCKED_METHODOLOGY_VERSIONS:
        raise PermissionError("blocked version cannot be a revision base")
    if proposal.kind == "METHODOLOGY_REVISION":
        revision = proposal.revision
        assert revision is not None
        if not base.mandatory_gates <= revision.mandatory_gates:
            raise ValueError("revision removes a mandatory gate")
        if not base.required_documents <= revision.required_documents:
            raise ValueError("revision removes a required document")
        if not any(step.type == "human_approval" for step in revision.definition.steps):
            raise ValueError("revision removes human approval")
        validate(revision.definition)
        return revision
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
            if qa.condition is not None:
                raise ValueError("QA already has a condition")
            steps[steps.index(qa)] = qa.model_copy(
                update={
                    "depends_on": [*qa.depends_on, change.gate],
                    "condition": Condition(step=change.gate, result_equals=True),
                }
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
        required_documents=base.required_documents,
    )


def semantic_diff(
    base: MethodologyVersion, revised: MethodologyVersion
) -> tuple[SemanticChange, ...]:
    def properties(
        version: MethodologyVersion,
    ) -> dict[tuple[ChangeCategory, str], str]:
        values: dict[tuple[ChangeCategory, str], str] = {}

        def put(category: ChangeCategory, subject: str, value: object) -> None:
            values[(category, subject)] = json.dumps(
                value, sort_keys=True, separators=(",", ":")
            )

        put("gate", "mandatory", sorted(version.mandatory_gates))
        put("gate", "documents", sorted(version.required_documents))
        put("limit", "workflow", version.definition.limits.model_dump(mode="json"))
        put("state", "metadata", version.definition.metadata)
        for step in version.definition.steps:
            put("state", step.id, step.type)
            put("agent", step.id, {
                "agent": step.agent, "executor": step.executor,
                "inputs": step.inputs, "outputs": step.outputs,
            })
            put("gate", step.id, step.validator)
            put("permission", step.id, step.sandbox_mode)
            put("transition", step.id, {
                "depends_on": step.depends_on,
                "condition": step.condition.model_dump(mode="json")
                if step.condition else None,
            })
            put("limit", step.id, {
                "timeout_seconds": step.timeout_seconds,
                "retry_policy": step.retry_policy.model_dump(mode="json"),
            })
        return values

    before, after = properties(base), properties(revised)
    return tuple(
        SemanticChange(
            category=category, subject=subject,
            before=before.get((category, subject)),
            after=after.get((category, subject)),
        )
        for category, subject in sorted(before.keys() | after.keys())
        if before.get((category, subject)) != after.get((category, subject))
    )


def revision_evidence_digest(
    db: sqlite3.Connection,
    run_ids: tuple[str, ...],
    candidate_version: MethodologyVersion | None = None,
) -> str:
    evidence = []
    for run_id in sorted(run_ids):
        run = db.execute(
            "SELECT definition,definition_hash,status FROM workflow_runs "
            "WHERE run_id=?", (run_id,),
        ).fetchone()
        if run is None:
            external = db.execute(
                "SELECT * FROM revision_external_evidence WHERE evidence_id=?",
                (run_id,),
            ).fetchone()
            if external is None or candidate_version is None:
                raise ValueError("revision evidence run is missing")
            if external["candidate_sha256"] != methodology_digest(candidate_version):
                raise ValueError("external evidence names another candidate")
            current = _source_evidence_digest(
                Path(external["source_database"]),
                str(external["source_run_id"]),
                Path(external["workspace_path"]),
                candidate_version,
            )
            if current != external["evidence_sha256"]:
                raise ValueError("external revision evidence changed")
            evidence.append({
                "evidence_id": run_id,
                "source_database": external["source_database"],
                "source_run_id": external["source_run_id"],
                "workspace_path": external["workspace_path"],
                "candidate_sha256": external["candidate_sha256"],
                "evidence_sha256": current,
            })
            continue
        definition = WorkflowDefinition.model_validate_json(run["definition"])
        if definition_hash(definition) != run["definition_hash"]:
            raise ValueError("revision evidence definition changed")
        steps = db.execute(
            "SELECT step_id,status,execution_id,result FROM workflow_steps "
            "WHERE run_id=? ORDER BY step_id", (run_id,),
        ).fetchall()
        executions = []
        for step in steps:
            if step["execution_id"] is None:
                continue
            execution = db.execute(
                "SELECT status,result FROM executions WHERE execution_id=?",
                (step["execution_id"],),
            ).fetchone()
            if execution is None:
                raise ValueError("revision evidence execution is missing")
            executions.append(dict(execution))
        snapshot = db.execute(
            "SELECT manifest,sha256,content FROM audit_artifact_snapshots "
            "WHERE run_id=?", (run_id,),
        ).fetchone()
        record = {
            "run_id": run_id,
            "definition_hash": run["definition_hash"],
            "status": run["status"],
            "steps": [dict(step) for step in steps],
            "executions": executions,
            "messages": [dict(row) for row in db.execute(
                "SELECT message_id,type,content FROM messages WHERE run_id=? "
                "ORDER BY message_id", (run_id,),
            ).fetchall()],
            "documents": [dict(row) for row in db.execute(
                "SELECT document_type,sha256,content FROM documentation_records "
                "WHERE run_id=? ORDER BY document_id", (run_id,),
            ).fetchall()],
            "snapshot": {
                "manifest": snapshot["manifest"],
                "sha256": snapshot["sha256"],
                "content_sha256": hashlib.sha256(snapshot["content"]).hexdigest(),
            } if snapshot else None,
        }
        attachments = {}
        for table in (
            "arena_comparisons",
            "tdd_red_snapshots",
            "tdd_review_snapshots",
            "workflow_adr_records",
        ):
            rows = db.execute(
                f"SELECT content,sha256 FROM {table} WHERE run_id=? ORDER BY sha256",
                (run_id,),
            ).fetchall()
            if rows:
                if any(digest(row["content"]) != row["sha256"] for row in rows):
                    raise ValueError(f"{table} evidence hash mismatch")
                attachments[table] = [dict(row) for row in rows]
        if attachments:
            task_input = db.execute(
                "SELECT content,sha256 FROM workflow_run_inputs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if task_input is not None:
                if digest(task_input["content"]) != task_input["sha256"]:
                    raise ValueError("workflow task input evidence hash mismatch")
                attachments["workflow_run_inputs"] = [dict(task_input)]
            record["attachments"] = attachments
        evidence.append(record)
    return digest(json.dumps(evidence, sort_keys=True, separators=(",", ":")))


def _source_evidence_digest(
    source_database: Path,
    run_id: str,
    workspace: Path,
    candidate_version: MethodologyVersion,
) -> str:
    source = source_database.resolve(strict=True)
    project = workspace.resolve(strict=True)
    with sqlite3.connect(
        f"file:{quote(str(source))}?mode=ro", uri=True
    ) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        run = db.execute(
            "SELECT definition,definition_hash,status FROM workflow_runs "
            "WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if run is None or run["status"] not in {"WAITING_APPROVAL", "COMPLETED"}:
            raise ValueError("source run is missing or not reviewable")
        definition = WorkflowDefinition.model_validate_json(run["definition"])
        expected = definition_hash(candidate_version.definition)
        if (
            definition_hash(definition) != expected
            or run["definition_hash"] != expected
        ):
            raise ValueError("source run differs from candidate")
        binding = db.execute(
            "SELECT methodology_id,version,sha256 FROM methodology_run_bindings "
            "WHERE run_id=?", (run_id,),
        ).fetchone()
        published = db.execute(
            "SELECT definition,sha256 FROM methodology_versions "
            "WHERE methodology_id=? AND version=?",
            (candidate_version.methodology_id, candidate_version.version),
        ).fetchone()
        if (
            binding is None or published is None
            or (binding["methodology_id"], binding["version"])
            != (candidate_version.methodology_id, candidate_version.version)
            or digest(published["definition"]) != published["sha256"]
            or binding["sha256"] != published["sha256"]
            or methodology_digest(
                MethodologyVersion.model_validate_json(published["definition"])
            ) != methodology_digest(candidate_version)
        ):
            raise ValueError("source methodology binding differs from candidate")
        steps = {
            row["step_id"]: row["status"] for row in db.execute(
                "SELECT step_id,status FROM workflow_steps WHERE run_id=?", (run_id,)
            ).fetchall()
        }
        if any(
            steps.get(step.id) != "SUCCEEDED"
            for step in candidate_version.definition.steps
            if step.type == "validation"
            and step.validator in candidate_version.mandatory_gates
        ):
            raise ValueError("source run lacks mandatory gates")
        if (
            candidate_version.definition.metadata.get("approval_binding")
            == "audit_artifact"
        ):
            snapshot = db.execute(
                "SELECT manifest,sha256,content FROM audit_artifact_snapshots "
                "WHERE run_id=?", (run_id,),
            ).fetchone()
            if snapshot is None or digest(snapshot["manifest"]) != snapshot["sha256"]:
                raise ValueError("source audit snapshot is missing or corrupt")
            manifest = json.loads(snapshot["manifest"])
            content = bytes(snapshot["content"])
            required = {
                "run_id", "methodology_id", "version", "methodology_sha256",
                "definition_hash", "base_commit", "commit", "path",
                "content_sha256",
            }
            if not isinstance(manifest, dict) or set(manifest) != required:
                raise ValueError("source audit manifest is invalid")
            if (
                manifest["run_id"] != run_id
                or manifest["methodology_id"] != candidate_version.methodology_id
                or manifest["version"] != candidate_version.version
                or manifest["methodology_sha256"] != published["sha256"]
                or manifest["definition_hash"] != expected
                or hashlib.sha256(content).hexdigest()
                != manifest["content_sha256"]
            ):
                raise ValueError("source audit snapshot does not match candidate")
            relative = Path(manifest["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("source audit path escapes the workspace")
            try:
                blob = subprocess.run(
                    ["git", "-C", str(project), "cat-file", "blob",
                     f"{manifest['commit']}:{manifest['path']}"],
                    check=True, capture_output=True, timeout=30,
                ).stdout
                head = subprocess.run(
                    ["git", "-C", str(project), "rev-parse", "HEAD"],
                    check=True, capture_output=True, timeout=30, text=True,
                ).stdout.strip()
                dirty = subprocess.run(
                    ["git", "-C", str(project), "status", "--porcelain"],
                    check=True, capture_output=True, timeout=30, text=True,
                ).stdout
            except subprocess.SubprocessError as exc:
                raise ValueError("source audit commit is unavailable") from exc
            raw_target = project / relative
            if raw_target.is_symlink():
                raise ValueError("source audit target is a symlink")
            target = raw_target.resolve(strict=True)
            if (
                project not in target.parents
                or blob != content
                or target.read_bytes() != content
                or head != manifest["commit"]
                or dirty
            ):
                raise ValueError("source audit artifact differs from snapshot")
        return revision_evidence_digest(db, (run_id,))


class MethodologyEvaluator:
    def __init__(self, store: ProposalStore, registry: MethodologyRegistry) -> None:
        self.store = store
        self.registry = registry

    def _begin(self, proposal_id: str) -> tuple[MethodologyProposal, str]:
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
        return proposal, sha

    def _finish(self, result: MethodologyEvaluation) -> MethodologyEvaluation:
        with self.store.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                "UPDATE methodology_proposals SET status=?,evaluation=? "
                "WHERE proposal_id=? AND status=?",
                (
                    ProposalStatus.EVALUATION_FAILED
                    if result.verdict == EvaluationVerdict.FAIL
                    else ProposalStatus.AWAITING_APPROVAL,
                    result.model_dump_json(),
                    result.proposal_id,
                    ProposalStatus.UNDER_EVALUATION,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("proposal state changed during evaluation")
            _event(
                db,
                result.proposal_id,
                result.verdict.value,
                detail=result.model_dump_json(),
            )
        return result

    def evaluate(
        self,
        proposal_id: str,
        *,
        cases: tuple[BenchmarkCase, ...] = (),
        benchmark: Callable[[WorkflowDefinition, BenchmarkCase], float] | None = None,
    ) -> MethodologyEvaluation:
        proposal, sha = self._begin(proposal_id)
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
        return self._finish(result)

    def evaluate_revision(
        self,
        proposal_id: str,
        verifier: Callable[[MethodologyVersion], RevisionVerification],
    ) -> MethodologyEvaluation:
        if self.store.get(proposal_id)[0].kind != "METHODOLOGY_REVISION":
            raise ValueError("proposal is not a full methodology revision")
        proposal, sha = self._begin(proposal_id)
        candidate_sha: str | None = None
        evidence_sha: str | None = None
        differences: tuple[SemanticChange, ...] = ()
        verification: RevisionVerification | None = None
        limitations: tuple[str, ...] = ()
        try:
            base = self.registry.get(proposal.methodology_id, proposal.base_version)
            revised = candidate(base, proposal)
            self.registry._validate_dependencies(revised)
            if proposal.proposed_version in self.registry.versions(
                proposal.methodology_id
            ):
                raise ValueError("proposed version is already published")
            candidate_sha = methodology_digest(revised)
            differences = semantic_diff(base, revised)
            if not differences:
                raise ValueError("revision has no semantic changes")
            verifier_input = revised.model_copy(deep=True)
            verification = verifier(verifier_input)
            if methodology_digest(verifier_input) != candidate_sha:
                raise ValueError("verifier mutated the candidate")
            if verification.candidate_sha256 != candidate_sha:
                raise ValueError("verification names another candidate")
            if set(verification.evidence_refs) != set(proposal.evidence_refs):
                raise ValueError("verification does not cover proposal evidence")
            with self.store.db.connect() as db:
                for run_id in proposal.evidence_refs:
                    run = db.execute(
                        "SELECT definition_hash,status FROM workflow_runs "
                        "WHERE run_id=?", (run_id,),
                    ).fetchone()
                    if run is None:
                        external = db.execute(
                            "SELECT 1 FROM revision_external_evidence "
                            "WHERE evidence_id=?", (run_id,),
                        ).fetchone()
                        if external is not None:
                            continue
                    if (
                        run is None
                        or run["definition_hash"] != definition_hash(revised.definition)
                        or run["status"] not in {"WAITING_APPROVAL", "COMPLETED"}
                    ):
                        raise ValueError("evidence run does not match candidate")
                    gate_rows = db.execute(
                        "SELECT step_id,status FROM workflow_steps WHERE run_id=?",
                        (run_id,),
                    ).fetchall()
                    gates = {row["step_id"]: row["status"] for row in gate_rows}
                    if any(
                        gates.get(step.id) != "SUCCEEDED"
                        for step in revised.definition.steps
                        if step.type == "validation"
                        and step.validator in revised.mandatory_gates
                    ):
                        raise ValueError("evidence run lacks mandatory gates")
                evidence_sha = revision_evidence_digest(
                    db, proposal.evidence_refs, revised
                )
            if len({check.name for check in verification.checks}) != len(
                verification.checks
            ):
                raise ValueError("duplicate revision check")
            limitations = verification.limitations
            verdict = (
                EvaluationVerdict.PASS_WITH_WARNINGS
                if all(check.passed for check in verification.checks)
                and limitations
                else EvaluationVerdict.PASS
                if all(check.passed for check in verification.checks)
                else EvaluationVerdict.FAIL
            )
            checks = tuple(
                f"{check.name}: {'PASS' if check.passed else 'FAIL'} "
                f"({check.evidence})"
                for check in verification.checks
            )
        except Exception as exc:
            verdict = EvaluationVerdict.FAIL
            checks = (f"{type(exc).__name__}: {exc}",)
        return self._finish(
            MethodologyEvaluation(
                evaluation_id=f"eval:{proposal_id}",
                proposal_id=proposal_id,
                proposal_sha256=sha,
                candidate_sha256=candidate_sha,
                evidence_sha256=evidence_sha,
                semantic_diff=differences,
                revision_checks=verification.checks if verification else (),
                verdict=verdict,
                checks=checks,
                limitations=limitations,
                evidence_kind="EXECUTED" if verification else "FIXTURE",
            )
        )

    async def evaluate_controlled(
        self, proposal_id: str, runner: LocalBenchmarkRunner
    ) -> MethodologyEvaluation:
        if self.store.get(proposal_id)[0].kind == "METHODOLOGY_REVISION":
            raise ValueError("full revision requires revision-specific verification")
        proposal, sha = self._begin(proposal_id)
        runs: tuple[BenchmarkRun, ...] = ()
        pairs: tuple[BenchmarkPair, ...] = ()
        checks: tuple[str, ...] = ()
        limitations = (
            "Scripted local adapter; the comparison is exploratory, not causal proof.",
            "The generic workflow does not measure review iterations or rework.",
        )
        try:
            base = self.registry.get(proposal.methodology_id, proposal.base_version)
            proposed = candidate(base, proposal)
            if proposal.proposed_version in self.registry.versions(
                proposal.methodology_id
            ):
                raise ValueError("proposed version is already published")
        except Exception as exc:
            verdict = EvaluationVerdict.FAIL
            checks = (f"{type(exc).__name__}: {exc}",)
        else:
            try:
                runs = await runner.compare(base, proposed)
                pairs = self._controlled_pairs(runs, proposal.expected_metric)
                if any(run.failure for run in runs):
                    verdict = EvaluationVerdict.INCONCLUSIVE
                    checks = ("benchmark runner failed",)
                elif any(
                    before.accepted and not after.accepted
                    for before, after in self._paired_runs(runs)
                ):
                    verdict = EvaluationVerdict.FAIL
                    checks = ("candidate regressed on an accepted task",)
                elif not pairs:
                    verdict = EvaluationVerdict.INCONCLUSIVE
                    checks = (f"metric unavailable: {proposal.expected_metric}",)
                else:
                    before_mean = sum(pair.baseline for pair in pairs) / len(pairs)
                    after_mean = sum(pair.candidate for pair in pairs) / len(pairs)
                    improved = (
                        after_mean < before_mean
                        if proposal.expected_direction == "decrease"
                        else after_mean > before_mean
                    )
                    verdict = (
                        EvaluationVerdict.PASS_WITH_WARNINGS
                        if improved
                        else EvaluationVerdict.FAIL
                    )
                    checks = (f"paired mean: {before_mean} -> {after_mean}",)
            except Exception as exc:
                verdict = EvaluationVerdict.INCONCLUSIVE
                checks = (f"benchmark error: {type(exc).__name__}: {exc}",)
        result = MethodologyEvaluation(
            evaluation_id=f"eval:{proposal_id}",
            proposal_id=proposal_id,
            proposal_sha256=sha,
            verdict=verdict,
            checks=checks,
            limitations=limitations,
            pairs=pairs,
            evidence_kind="EXECUTED",
            benchmark_runs=runs,
            baseline=sum(pair.baseline for pair in pairs) / len(pairs)
            if pairs
            else None,
            candidate=sum(pair.candidate for pair in pairs) / len(pairs)
            if pairs
            else None,
        )
        return self._finish(result)

    @staticmethod
    def _paired_runs(
        runs: tuple[BenchmarkRun, ...],
    ) -> list[tuple[BenchmarkRun, BenchmarkRun]]:
        grouped: dict[tuple[str, int], dict[str, BenchmarkRun]] = {}
        for run in runs:
            group = grouped.setdefault((run.task_id, run.repetition), {})
            if run.arm in group:
                raise ValueError("duplicate benchmark arm")
            group[run.arm] = run
        if not grouped or any(
            set(group) != {"baseline", "candidate"} for group in grouped.values()
        ):
            raise ValueError("incomplete benchmark pairs")
        paired = []
        for group in grouped.values():
            before, after = group["baseline"], group["candidate"]
            if (
                before.acceptance_sha256 != after.acceptance_sha256
                or before.conditions_sha256 != after.conditions_sha256
            ):
                raise ValueError("benchmark conditions or criteria differ")
            paired.append((before, after))
        return paired

    @classmethod
    def _controlled_pairs(
        cls, runs: tuple[BenchmarkRun, ...], metric: str
    ) -> tuple[BenchmarkPair, ...]:
        if metric not in {
            "acceptance_rate",
            "agent_invocation_count",
            "duration_seconds",
        }:
            return ()
        pairs = []
        for before, after in cls._paired_runs(runs):
            if metric != "acceptance_rate" and (
                not before.accepted or not after.accepted
            ):
                return ()
            baseline = (
                float(before.accepted)
                if metric == "acceptance_rate"
                else float(getattr(before, metric))
            )
            candidate_value = (
                float(after.accepted)
                if metric == "acceptance_rate"
                else float(getattr(after, metric))
            )
            pairs.append(
                BenchmarkPair(
                    task_id=f"{before.task_id}-{before.repetition}",
                    acceptance_sha256=before.acceptance_sha256,
                    conditions=f"sha256:{before.conditions_sha256}",
                    baseline=baseline,
                    candidate=candidate_value,
                )
            )
        return tuple(pairs)


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
        *,
        candidate_sha256: str | None = None,
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
            assessment = MethodologyEvaluation.model_validate_json(evaluation)
            if proposal.kind == "METHODOLOGY_REVISION":
                if (
                    candidate_sha256 != proposal.candidate_sha256
                    or assessment.candidate_sha256 != candidate_sha256
                    or assessment.proposal_sha256 != sha
                ):
                    raise ValueError("approval does not match evaluated candidate")
                if decision == "APPROVED" and assessment.evidence_sha256 != (
                    revision_evidence_digest(
                        db, proposal.evidence_refs, proposal.revision
                    )
                ):
                    raise ValueError("revision evidence changed after evaluation")
            elif candidate_sha256 is not None:
                raise ValueError("legacy proposal has no candidate digest")
            if decision == "APPROVED" and (
                assessment.evidence_kind != "EXECUTED"
                or assessment.verdict
                not in {EvaluationVerdict.PASS, EvaluationVerdict.PASS_WITH_WARNINGS}
            ):
                raise ValueError("executed evaluation required for approval")
            result = db.execute(
                "UPDATE methodology_proposals SET status=?,approval_by=?,approval_at=?,"
                "approval_candidate_sha256=? "
                "WHERE proposal_id=? AND status=? AND sha256=?",
                (
                    decision,
                    actor,
                    decided_at.isoformat(),
                    candidate_sha256,
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
            candidate_sha256=candidate_sha256,
            actor=actor,
            decided_at=decided_at,
            decision=decision,
        )

    def publish(self, proposal_id: str, actor: str) -> MethodologyPublication:
        if actor not in self.local_approvers:
            raise PermissionError("local publisher is not authorized")
        proposal, _, sha = self.store.get(proposal_id)
        if (
            proposal.methodology_id, proposal.proposed_version
        ) in BLOCKED_METHODOLOGY_VERSIONS:
            raise PermissionError("methodology version is blocked")
        with self.store.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,sha256,approval_by,approval_at,evaluation,"
                "approval_candidate_sha256 "
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
            if (
                evaluation.proposal_sha256 != sha
                or evaluation.evidence_kind != "EXECUTED"
                or evaluation.verdict
                not in {EvaluationVerdict.PASS, EvaluationVerdict.PASS_WITH_WARNINGS}
            ):
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
            if proposal.kind == "METHODOLOGY_REVISION":
                current_sha = methodology_digest(version)
                if (
                    row["approval_candidate_sha256"] != current_sha
                    or evaluation.candidate_sha256 != current_sha
                    or evaluation.semantic_diff != semantic_diff(base, version)
                    or not evaluation.revision_checks
                    or not all(check.passed for check in evaluation.revision_checks)
                    or evaluation.evidence_sha256 != revision_evidence_digest(
                        db, proposal.evidence_refs, version
                    )
                ):
                    raise PermissionError("approved candidate changed")
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
