from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from aec.communication.broker import Broker
from aec.communication.schemas import (
    ApprovalRequest,
    ArtifactRef,
    Message,
    MessageType,
    TaskCompleted,
    TaskFailed,
    ValidationResult,
)
from aec.communication.schemas import (
    ReworkRequest as ACPReworkRequest,
)
from aec.execution.locking import LocalLock, held
from aec.execution.manager import ExecutionManager
from aec.execution.models import ExecutionResult
from aec.methodology.governance import MethodologyRegistry
from aec.security.identities import Identity, Role
from aec.workflows.engine import WorkflowEngine
from aec.workflows.models import WorkflowDefinition, WorkflowStatus

from .gates import CommandGate, GateResult, run_gates
from .git import capture, changed_files, commit_hash, create_worktree, git
from .models import ImplementationResult, QAReviewResult, ReworkRequest, Severity


class AdversarialLoop:
    def __init__(
        self,
        broker: Broker,
        manager: ExecutionManager,
        orchestrator: Identity,
        implementer: Identity,
        qa: Identity,
        quality_gate: Identity,
        repository: Path,
        worktree_root: Path,
        gates: tuple[CommandGate, ...],
        *,
        executor: str = "codex",
        max_iterations: int = 3,
        max_duration_seconds: float = 1800,
        local_approvers: frozenset[str] = frozenset(),
        methodology_version: str | None = None,
    ) -> None:
        for identity, role in (
            (orchestrator, Role.ORCHESTRATOR),
            (implementer, Role.IMPLEMENTER),
            (qa, Role.QA),
            (quality_gate, Role.QUALITY_GATE),
        ):
            broker._identity(identity)
            if identity.role != role:
                raise PermissionError(f"{role.value} identity required")
        if (
            max_iterations < 1
            or max_duration_seconds <= 0
            or executor not in manager.adapters
            or not gates
            or len({gate.gate_id for gate in gates}) != len(gates)
        ):
            raise ValueError("positive iteration limit, executor and gates required")
        if (
            manager.budget is not None and manager.budget.version == "2"
            and manager.budget.max_iterations is not None
            and max_iterations > manager.budget.max_iterations
        ):
            raise ValueError("iteration count exceeds execution policy")
        self.broker = broker
        self.manager = manager
        self.orchestrator = orchestrator
        self.implementer = implementer
        self.qa = qa
        self.quality_gate = quality_gate
        self.repository = repository.resolve(strict=True)
        self.worktree_root = worktree_root.resolve()
        self.manager.policy.require(self.repository)
        self.manager.policy.require(self.worktree_root)
        if (
            self.worktree_root == self.repository
            or self.repository in self.worktree_root.parents
        ):
            raise ValueError("worktrees must be outside the main repository")
        self.gates = gates
        self.executor = executor
        self.max_iterations = max_iterations
        self.max_duration_seconds = max_duration_seconds
        self.local_approvers = local_approvers
        self.methodology_version = methodology_version
        self.pre_qa_gate_ids: frozenset[str] = frozenset()
        if methodology_version is not None:
            published = MethodologyRegistry(broker.db).get(
                "dev-adversarial", methodology_version
            )
            steps = {step.id: step for step in published.definition.steps}
            if set(steps) - {"implement", "qa", "approval"} - {
                gate.gate_id for gate in gates
            }:
                raise ValueError("published methodology has unsupported steps")
            implement = steps.get("implement")
            qa_step = steps.get("qa")
            approval = steps.get("approval")
            if (
                implement is None
                or implement.type != "agent"
                or implement.agent != "implementer"
                or implement.depends_on
                or qa_step is None
                or qa_step.type != "agent"
                or qa_step.agent != "qa"
                or approval is None
                or approval.type != "human_approval"
                or approval.depends_on != ["qa"]
                or "implement" not in qa_step.depends_on
            ):
                raise ValueError("published methodology is not an adversarial loop")
            pre_ids = set(qa_step.depends_on) - {"implement"}
            if any(
                gate_id not in steps
                or steps[gate_id].type != "validation"
                or steps[gate_id].validator != gate_id
                or steps[gate_id].depends_on != ["implement"]
                for gate_id in pre_ids
            ) or set(steps) != {"implement", "qa", "approval"} | pre_ids:
                raise ValueError("published methodology has unsupported gate order")
            if not pre_ids <= {gate.gate_id for gate in gates}:
                raise ValueError("published methodology requires unconfigured gate")
            self.pre_qa_gate_ids = frozenset(pre_ids)
        self._active: set[str] = set()

    def _row(self, run_id: str) -> dict[str, str | int]:
        with self.broker.db.connect() as db:
            row = db.execute(
                "SELECT * FROM engineering_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        with self.broker.db.connect() as db:
            binding = db.execute(
                "SELECT version FROM methodology_run_bindings WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if (binding["version"] if binding else None) != self.methodology_version:
            raise ValueError("engineering run uses another methodology version")
        return dict(row)

    def _iteration(self, run_id: str, number: int) -> dict[str, str | int | None]:
        with self.broker.db.connect() as db:
            row = db.execute(
                "SELECT * FROM engineering_iterations WHERE run_id=? AND number=?",
                (run_id, number),
            ).fetchone()
        if row is None:
            raise KeyError((run_id, number))
        return dict(row)

    def create(
        self,
        task_id: str,
        specification: str,
        criteria: tuple[str, ...],
        allowed_paths: tuple[str, ...],
        *,
        run_id: str | None = None,
    ) -> str:
        if not task_id or not specification or not criteria or not allowed_paths:
            raise ValueError("task, specification, criteria and allowed paths required")
        if any(
            not path or Path(path).is_absolute() or ".." in Path(path).parts
            for path in allowed_paths
        ):
            raise ValueError("allowed paths must be relative and confined")
        identifier = run_id or str(uuid4())
        if not identifier.replace("-", "").replace("_", "").isalnum():
            raise ValueError("run_id contains unsafe characters")
        base = commit_hash(self.repository)
        worktree = self.worktree_root / f"{identifier}-implementer"
        create_worktree(self.repository, worktree, base)
        with self.broker.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO engineering_runs VALUES "
                "(?,?,?,?,?,?,?,?,?,1,?,?,?,'RUNNING',NULL,?)",
                (
                    identifier,
                    task_id,
                    specification,
                    json.dumps(criteria),
                    json.dumps(allowed_paths),
                    str(self.repository),
                    str(worktree),
                    base,
                    base,
                    self.max_iterations,
                    self.max_duration_seconds,
                    json.dumps([gate.model_dump(mode="json") for gate in self.gates]),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO engineering_iterations(run_id,number,status) "
                "VALUES (?,1,'PREPARED')",
                (identifier,),
            )
            if self.methodology_version is not None:
                published = db.execute(
                    "SELECT sha256 FROM methodology_versions "
                    "WHERE methodology_id='dev-adversarial' AND version=?",
                    (self.methodology_version,),
                ).fetchone()
                if published is None:
                    raise ValueError("selected methodology is no longer published")
                db.execute(
                    "INSERT INTO methodology_run_bindings VALUES (?,?,?,?)",
                    (
                        identifier,
                        "dev-adversarial",
                        self.methodology_version,
                        published["sha256"],
                    ),
                )
        return identifier

    def _engine(
        self, implementer_path: Path, qa_path: Path | None = None
    ) -> WorkflowEngine:
        paths = {"implementer": implementer_path}
        if qa_path is not None:
            paths["qa"] = qa_path
        return WorkflowEngine(
            self.broker,
            self.manager,
            self.orchestrator,
            {"implementer": self.implementer, "qa": self.qa},
            self.quality_gate,
            implementer_path,
            {},
            workspaces=paths,
            local_approvers=self.local_approvers,
        )

    async def _agent(
        self,
        run_id: str,
        number: int,
        stage: str,
        path: Path,
        prompt: str,
        artifact_id: str = "",
    ) -> tuple[str, list[ArtifactRef]]:
        workflow_id = f"{run_id}-i{number}-{stage}"
        engine = self._engine(
            Path(str(self._row(run_id)["worktree_path"])),
            path if stage == "review" else None,
        )
        definition = WorkflowDefinition.model_validate(
            {
                "id": "dev-adversarial",
                "version": self.methodology_version or "1.0.0",
                "name": "Adversarial Engineering Loop",
                "metadata": {
                    "task_id": str(self._row(run_id)["task_id"]),
                    "iteration": str(number),
                    "engineering_run_id": run_id,
                },
                "steps": [
                    {
                        "id": stage,
                        "type": "agent",
                        "agent": "qa" if stage == "review" else "implementer",
                        "executor": self.executor,
                        "sandbox_mode": "read-only"
                        if stage == "review"
                        else "workspace-write",
                        "inputs": {"prompt": prompt, "artifact_id": artifact_id},
                    }
                ],
            }
        )
        try:
            workflow = engine.repository.get_run(workflow_id)
        except KeyError:
            workflow = engine.create(definition, workflow_id)
        if workflow.status in {WorkflowStatus.VALIDATED, WorkflowStatus.RUNNING}:
            workflow = await engine.run(workflow_id)
        if workflow.status != WorkflowStatus.COMPLETED:
            raise RuntimeError(f"{stage} workflow ended {workflow.status}")
        step = engine.repository.steps(workflow_id)[stage]
        if step.result is None or not isinstance(step.result.get("output"), str):
            raise RuntimeError("agent result missing")
        execution = self.manager.repository.get(str(step.execution_id))
        if not isinstance(execution, ExecutionResult):
            raise RuntimeError("execution result missing")
        for ref in execution.artifact_refs:
            if not self._artifact_intact(ref):
                raise RuntimeError("execution artifact integrity failed")
        return str(step.result["output"]), execution.artifact_refs

    def _artifact_intact(self, ref: ArtifactRef) -> bool:
        source_run = urlsplit(ref.uri).netloc
        if ref.uri != f"artifact://{source_run}/{ref.artifact_id}":
            return False
        artifact = (
            Path(self.broker.db.path).resolve().parent
            / "artifacts"
            / hashlib.sha256(source_run.encode()).hexdigest()
            / ref.artifact_id
        )
        return (
            artifact.is_file()
            and hashlib.sha256(artifact.read_bytes()).hexdigest() == ref.sha256
        )

    def _qa_evidence_valid(
        self, run_id: str, number: int, review: QAReviewResult
    ) -> bool:
        known = [
            ref
            for stage in ("implement", "review")
            for message in self.broker.get_history(f"{run_id}-i{number}-{stage}")
            for ref in message.artifact_refs
        ]
        supplied = review.evidence_refs + [
            ref for finding in review.findings for ref in finding.evidence_refs
        ]
        return all(ref in known and self._artifact_intact(ref) for ref in supplied)

    def _save(
        self,
        run_id: str,
        number: int,
        status: str,
        *,
        implementation: ImplementationResult | None = None,
        review: QAReviewResult | None = None,
        gates: list[GateResult] | None = None,
        head: str | None = None,
    ) -> None:
        with self.broker.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE engineering_iterations SET status=?,"
                "implementation=COALESCE(?,implementation),"
                "review=COALESCE(?,review),gates=COALESCE(?,gates) "
                "WHERE run_id=? AND number=?",
                (
                    status,
                    implementation.model_dump_json() if implementation else None,
                    review.model_dump_json() if review else None,
                    json.dumps([gate.model_dump(mode="json") for gate in gates])
                    if gates is not None
                    else None,
                    run_id,
                    number,
                ),
            )
            if head is not None:
                db.execute(
                    "UPDATE engineering_runs SET head_commit=? WHERE run_id=?",
                    (head, run_id),
                )

    def _recover_snapshot(
        self, run_id: str, number: int, row: dict[str, str | int]
    ) -> bool:
        """Finish the database write after a controller commit survived a crash."""
        workflow_id = f"{run_id}-i{number}-implement"
        engine = self._engine(Path(str(row["worktree_path"])))
        try:
            workflow = engine.repository.get_run(workflow_id)
            step = engine.repository.steps(workflow_id)["implement"]
            execution = self.manager.repository.get(str(step.execution_id))
            worktree = Path(str(row["worktree_path"]))
            base = str(row["head_commit"])
            head = commit_hash(worktree)
            files = git(worktree, "diff", "--name-only", base, head).splitlines()
            allowed = tuple(json.loads(str(row["allowed_paths"])))
            if (
                workflow.status != WorkflowStatus.COMPLETED
                or not isinstance(execution, ExecutionResult)
                or execution.status != "SUCCEEDED"
                or execution.agent_id != self.implementer.agent_id
                or not isinstance(step.result, dict)
                or not isinstance(step.result.get("output"), str)
                or step.result["output"] != execution.output
                or git(worktree, "show", "-s", "--format=%P", head) != base
                or git(worktree, "show", "-s", "--format=%s", head)
                != "AEC implementation snapshot"
                or git(worktree, "show", "-s", "--format=%an <%ae>", head)
                != "AEC <aec@localhost>"
                or not files
                or changed_files(worktree)
                or not execution.artifact_refs
                or any(
                    not any(
                        file == prefix or file.startswith(prefix.rstrip("/") + "/")
                        for prefix in allowed
                    )
                    or not (worktree / file).is_file()
                    or (worktree / file).is_symlink()
                    for file in files
                )
                or any(
                    not self._artifact_intact(ref) for ref in execution.artifact_refs
                )
            ):
                return False
        except (KeyError, OSError, RuntimeError, ValueError):
            return False
        implementation = ImplementationResult(
            task_id=str(row["task_id"]),
            iteration=number,
            status="SUCCEEDED",
            base_commit=base,
            result_commit=head,
            changed_files=files,
            artifact_refs=execution.artifact_refs,
            implementation_summary=execution.output,
        )
        self._save(
            run_id, number, "IMPLEMENTED", implementation=implementation, head=head
        )
        return True

    async def run(self, run_id: str) -> str:
        if run_id in self._active:
            raise RuntimeError("engineering run is already active")
        async with held(self.broker.db.path, f"engineering:{run_id}"):
            if run_id in self._active:
                raise RuntimeError("engineering run is already active")
            self._active.add(run_id)
            try:
                return await self._run(run_id)
            finally:
                self._active.remove(run_id)

    async def _run(self, run_id: str) -> str:
        while True:
            row = self._row(run_id)
            status = str(row["status"])
            if status != "RUNNING":
                if status == "WAITING_APPROVAL" and not self._final_snapshot_valid(row):
                    self._block(run_id, "artifact changed after validation")
                    return "BLOCKED"
                if status == "BLOCKED":
                    self._publish_block(run_id, str(row["error"]))
                if status in {"APPROVED", "REJECTED"}:
                    self._publish_decision(run_id, status == "APPROVED")
                return status
            if (
                row["max_iterations"] != self.max_iterations
                or row["max_duration_seconds"] != self.max_duration_seconds
                or row["gate_config"]
                != json.dumps([gate.model_dump(mode="json") for gate in self.gates])
            ):
                self._block(run_id, "methodology configuration changed")
                return "BLOCKED"
            if (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(str(row["created_at"]))
            ).total_seconds() >= self.max_duration_seconds:
                self._block(run_id, "engineering duration limit reached")
                return "BLOCKED"
            number = int(row["iteration"])
            current = self._iteration(run_id, number)
            worktree = Path(str(row["worktree_path"]))
            try:
                head_matches = commit_hash(worktree) == row["head_commit"]
            except (OSError, RuntimeError, ValueError):
                head_matches = False
            if not head_matches:
                if current["status"] == "PREPARED" and self._recover_snapshot(
                    run_id, number, row
                ):
                    continue
                self._block(
                    run_id, "implementation head changed outside the controller"
                )
                return "BLOCKED"
            criteria = tuple(json.loads(str(row["criteria"])))
            allowed = tuple(json.loads(str(row["allowed_paths"])))
            try:
                if current["status"] == "PREPARED":
                    feedback = ""
                    if number > 1:
                        previous = self._iteration(run_id, number - 1)
                        feedback = str(previous["review"])
                    prompt = json.dumps(
                        {
                            "task": row["specification"],
                            "criteria": criteria,
                            "allowed_paths": allowed,
                            "base_commit": row["head_commit"],
                            "iteration": number,
                            "feedback": feedback,
                            "instructions": (
                                "Implement the task in this worktree. Change only "
                                "allowed_paths. Do not commit, merge or push. "
                                "Return a short implementation summary."
                            ),
                        },
                        ensure_ascii=False,
                    )
                    output, refs = await self._agent(
                        run_id, number, "implement", worktree, prompt
                    )
                    base = str(row["head_commit"])
                    if commit_hash(worktree) != base:
                        raise RuntimeError("agent changed Git HEAD")
                    head, files = capture(worktree, allowed)
                    implementation = ImplementationResult(
                        task_id=str(row["task_id"]),
                        iteration=number,
                        status="SUCCEEDED",
                        base_commit=base,
                        result_commit=head,
                        changed_files=files,
                        artifact_refs=refs,
                        implementation_summary=output,
                    )
                    self._save(
                        run_id,
                        number,
                        "IMPLEMENTED",
                        implementation=implementation,
                        head=head,
                    )
                    continue
                if current["status"] == "IMPLEMENTED":
                    implementation = ImplementationResult.model_validate_json(
                        str(current["implementation"])
                    )
                    snapshot = self.worktree_root / f"{run_id}-qa-{number}"
                    if not snapshot.exists():
                        create_worktree(
                            self.repository, snapshot, implementation.result_commit
                        )
                    if commit_hash(
                        snapshot
                    ) != implementation.result_commit or changed_files(snapshot):
                        raise RuntimeError("QA snapshot is not stable")
                    if self.pre_qa_gate_ids:
                        if current["gates"] is None:
                            pre_gates = run_gates(
                                snapshot,
                                implementation.result_commit,
                                tuple(
                                    gate
                                    for gate in self.gates
                                    if gate.gate_id in self.pre_qa_gate_ids
                                ),
                                policy=self.manager.policy,
                            )
                            self._publish_gates(
                                run_id,
                                number,
                                str(row["task_id"]),
                                implementation.result_commit,
                                pre_gates,
                            )
                            self._save(run_id, number, "IMPLEMENTED", gates=pre_gates)
                            continue
                        pre_gates = [
                            GateResult.model_validate_json(json.dumps(item))
                            for item in json.loads(str(current["gates"]))
                        ]
                        if (
                            not self.pre_qa_gate_ids
                            <= {gate.gate_id for gate in pre_gates}
                            or any(gate.status != "PASSED" for gate in pre_gates)
                        ):
                            self._block(run_id, "pre-QA methodology gate failed")
                            return "BLOCKED"
                    diff = git(
                        worktree,
                        "diff",
                        implementation.base_commit,
                        implementation.result_commit,
                    )
                    if len(diff.encode()) > 40000:
                        raise RuntimeError("diff exceeds QA context limit")
                    prompt = json.dumps(
                        {
                            "task": row["specification"],
                            "criteria": criteria,
                            "reviewed_commit": implementation.result_commit,
                            "iteration": number,
                            "diff": diff,
                            "instructions": (
                                "Review the checked-out code and diff independently. "
                                "Do not modify files. Return only one JSON object "
                                "matching output_contract. Mark an issue REPRODUCED "
                                "only with reproduction steps or evidence."
                            ),
                            "output_contract": {
                                "task_id": row["task_id"],
                                "iteration": number,
                                "reviewed_commit": implementation.result_commit,
                                "review_status": "PASSED | FAILED | INCONCLUSIVE",
                                "findings": [
                                    {
                                        "finding_id": "unique ID",
                                        "severity": (
                                            "CRITICAL | HIGH | MEDIUM | LOW | INFO"
                                        ),
                                        "category": "category",
                                        "title": "title",
                                        "description": "description",
                                        "status": "REPRODUCED | HYPOTHESIS",
                                        "reproduction_steps": ["steps if reproduced"],
                                    }
                                ],
                            },
                        },
                        ensure_ascii=False,
                    )
                    output, refs = await self._agent(
                        run_id,
                        number,
                        "review",
                        snapshot,
                        prompt,
                        implementation.result_commit,
                    )
                    review = QAReviewResult.model_validate_json(output)
                    if not self._qa_evidence_valid(run_id, number, review):
                        raise ValueError("QA supplied nonexistent or altered evidence")
                    if (
                        review.task_id != row["task_id"]
                        or review.iteration != number
                        or review.reviewed_commit != implementation.result_commit
                    ):
                        raise ValueError("QA reviewed wrong task, iteration or commit")
                    if (
                        changed_files(snapshot)
                        or commit_hash(snapshot) != implementation.result_commit
                    ):
                        raise RuntimeError("QA modified its snapshot")
                    review.evidence_refs.extend(refs)
                    self._save(run_id, number, "REVIEWED", review=review)
                    continue
                if current["status"] == "REVIEWED":
                    implementation = ImplementationResult.model_validate_json(
                        str(current["implementation"])
                    )
                    review = QAReviewResult.model_validate_json(str(current["review"]))
                    snapshot = self.worktree_root / f"{run_id}-qa-{number}"
                    gates = run_gates(
                        snapshot,
                        review.reviewed_commit,
                        tuple(
                            gate
                            for gate in self.gates
                            if gate.gate_id not in self.pre_qa_gate_ids
                        ),
                        policy=self.manager.policy,
                    )
                    if self.pre_qa_gate_ids:
                        gates = [
                            GateResult.model_validate_json(json.dumps(item))
                            for item in json.loads(str(current["gates"]))
                            if item["gate_id"] != "snapshot-integrity"
                        ] + gates
                    self._publish_gates(
                        run_id,
                        number,
                        str(row["task_id"]),
                        review.reviewed_commit,
                        gates,
                    )
                    self._save(run_id, number, "GATED", gates=gates)
                    continue
                if current["status"] == "GATED":
                    review = QAReviewResult.model_validate_json(str(current["review"]))
                    gates = [
                        GateResult.model_validate_json(json.dumps(g))
                        for g in json.loads(str(current["gates"]))
                    ]
                    blockers = [
                        f
                        for f in review.findings
                        if f.status == "REPRODUCED"
                        and f.severity
                        in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
                    ]
                    hypotheses = [
                        f
                        for f in review.findings
                        if f.status == "HYPOTHESIS"
                        and f.severity in {Severity.CRITICAL, Severity.HIGH}
                    ]
                    if (
                        review.review_status == "PASSED"
                        and not blockers
                        and not hypotheses
                        and all(g.status == "PASSED" for g in gates)
                    ):
                        await self._approval(run_id, number)
                        return "WAITING_APPROVAL"
                    if review.review_status != "FAILED" or not blockers or hypotheses:
                        self._block(
                            run_id, "inconclusive review or deterministic gate failure"
                        )
                        return "BLOCKED"
                    if number >= self.max_iterations:
                        self._block(run_id, "iteration limit reached")
                        return "BLOCKED"
                    self._rework(run_id, number, review, criteria)
                    with self.broker.db.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "UPDATE engineering_runs SET iteration=? WHERE run_id=?",
                            (number + 1, run_id),
                        )
                        db.execute(
                            "INSERT INTO engineering_iterations(run_id,number,status) "
                            "VALUES (?,?,'PREPARED')",
                            (run_id, number + 1),
                        )
                    continue
                raise RuntimeError("invalid iteration state")
            except Exception as exc:
                if self._row(run_id)["status"] == "CANCELLED":
                    return "CANCELLED"
                self._block(run_id, f"{type(exc).__name__}: {exc}")
                return "BLOCKED"

    def _publish_gates(
        self,
        run_id: str,
        number: int,
        task_id: str,
        commit: str,
        gates: list[GateResult],
    ) -> None:
        for gate in gates:
            message_id = f"gate:{run_id}:{number}:{gate.gate_id}"
            if self.broker.get_message(message_id) is not None:
                continue
            self.broker.publish(
                Message(
                    protocol="ACP",
                    version="0.1",
                    message_id=message_id,
                    run_id=run_id,
                    task_id=task_id,
                    sender=self.quality_gate.agent_id,
                    recipient=self.orchestrator.agent_id,
                    type=MessageType.VALIDATION_RESULT,
                    iteration=number,
                    created_at=gate.timestamp,
                    payload=ValidationResult(
                        artifact_id=commit,
                        passed=gate.status == "PASSED",
                        evidence=f"{gate.gate_id}: {gate.evidence}",
                    ),
                    metadata={"gate_id": gate.gate_id, "reviewed_commit": commit},
                ),
                self.quality_gate,
            )

    def _rework(
        self,
        run_id: str,
        number: int,
        review: QAReviewResult,
        criteria: tuple[str, ...],
    ) -> None:
        if self.broker.get_message(f"rework:{run_id}:{number}") is not None:
            return
        row = self._row(run_id)
        findings = [
            f
            for f in review.findings
            if f.status == "REPRODUCED"
            and f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
        ]
        request = ReworkRequest(
            task_id=str(row["task_id"]),
            iteration=number,
            target_agent=self.implementer.agent_id,
            base_commit=str(row["head_commit"]),
            findings_to_address=[f.finding_id for f in findings],
            required_changes=[f.description for f in findings],
            acceptance_criteria=list(criteria),
            max_remaining_iterations=self.max_iterations - number,
        )
        self.broker.publish(
            Message(
                protocol="ACP",
                version="0.1",
                message_id=f"rework:{run_id}:{number}",
                run_id=run_id,
                task_id=request.task_id,
                sender=self.orchestrator.agent_id,
                recipient=self.implementer.agent_id,
                type=MessageType.REWORK_REQUEST,
                iteration=number,
                created_at=datetime.now(timezone.utc),
                payload=ACPReworkRequest(
                    finding_message_id=f"finding:workflow:{run_id}-i{number}-review:review:1:{findings[0].finding_id}",
                    instructions=request.model_dump_json(),
                ),
                metadata={"base_commit": request.base_commit},
            ),
            self.orchestrator,
        )

    async def _approval(self, run_id: str, number: int) -> None:
        row = self._row(run_id)
        engine = self._engine(Path(str(row["worktree_path"])))
        approval_id = f"{run_id}-approval"
        definition = WorkflowDefinition.model_validate(
            {
                "id": "dev-adversarial",
                "version": self.methodology_version or "1.0.0",
                "name": "Adversarial Engineering Loop",
                "steps": [{"id": "approval", "type": "human_approval"}],
            }
        )
        try:
            approval = engine.repository.get_run(approval_id)
        except KeyError:
            approval = engine.create(definition, approval_id)
        if approval.status in {WorkflowStatus.VALIDATED, WorkflowStatus.RUNNING}:
            approval = await engine.run(approval_id)
        if approval.status != WorkflowStatus.WAITING_APPROVAL:
            raise RuntimeError("approval workflow did not suspend")
        if self.broker.get_message(f"approval:{run_id}") is None:
            self.broker.publish(
                Message(
                    protocol="ACP",
                    version="0.1",
                    message_id=f"approval:{run_id}",
                    run_id=run_id,
                    task_id=str(row["task_id"]),
                    sender=self.orchestrator.agent_id,
                    recipient=self.orchestrator.agent_id,
                    type=MessageType.APPROVAL_REQUEST,
                    iteration=number,
                    created_at=datetime.now(timezone.utc),
                    payload=ApprovalRequest(
                        subject=str(row["task_id"]),
                        reason=(
                            f"Review and gates passed at {row['head_commit']}; "
                            "inspect engineering history before approving"
                        ),
                    ),
                ),
                self.orchestrator,
            )
        with self.broker.db.connect() as db:
            updated = db.execute(
                "UPDATE engineering_runs SET status='WAITING_APPROVAL' "
                "WHERE run_id=? AND status='RUNNING'",
                (run_id,),
            )
        if updated.rowcount != 1:
            raise RuntimeError("approval was cancelled")

    def _block(self, run_id: str, reason: str) -> None:
        with self.broker.db.connect() as db:
            db.execute(
                "UPDATE engineering_runs SET status='BLOCKED',error=? WHERE run_id=?",
                (reason, run_id),
            )
        self._publish_block(run_id, reason)

    def _publish_block(self, run_id: str, reason: str) -> None:
        if self.broker.get_message(f"blocked:{run_id}") is not None:
            return
        self.broker.publish(
            Message(
                protocol="ACP",
                version="0.1",
                message_id=f"blocked:{run_id}",
                run_id=run_id,
                task_id=str(self._row(run_id)["task_id"]),
                sender=self.orchestrator.agent_id,
                recipient=self.quality_gate.agent_id,
                type=MessageType.TASK_FAILED,
                iteration=int(self._row(run_id)["iteration"]),
                created_at=datetime.now(timezone.utc),
                payload=TaskFailed(reason=reason),
            ),
            self.orchestrator,
        )

    def decide(self, run_id: str, actor: str, approved: bool) -> str:
        if self._row(run_id)["status"] != "WAITING_APPROVAL":
            raise ValueError("run has no pending approval")
        row = self._row(run_id)
        if not self._final_snapshot_valid(row):
            self._block(run_id, "artifact changed after validation")
            raise ValueError("artifact changed after validation")
        engine = self._engine(Path(str(row["worktree_path"])))
        approval_id = f"{run_id}-approval"
        workflow = engine.repository.get_run(approval_id)
        if workflow.status == WorkflowStatus.VALIDATED:
            raise RuntimeError("approval workflow was not started")
        engine.decide(approval_id, "approval", actor, approved)
        status = "APPROVED" if approved else "REJECTED"
        with self.broker.db.connect() as db:
            db.execute(
                "UPDATE engineering_runs SET status=? WHERE run_id=?", (status, run_id)
            )
        self._publish_decision(run_id, approved)
        return status

    def _publish_decision(self, run_id: str, approved: bool) -> None:
        if self.broker.get_message(f"decision:{run_id}") is not None:
            return
        row = self._row(run_id)
        self.broker.publish(
            Message(
                protocol="ACP",
                version="0.1",
                message_id=f"decision:{run_id}",
                run_id=run_id,
                task_id=str(row["task_id"]),
                sender=self.orchestrator.agent_id,
                recipient=self.quality_gate.agent_id,
                type=MessageType.TASK_COMPLETED
                if approved
                else MessageType.TASK_FAILED,
                iteration=int(row["iteration"]),
                created_at=datetime.now(timezone.utc),
                payload=(
                    TaskCompleted(
                        summary="Human approval recorded; no merge or push",
                        validation_message_id=(
                            f"gate:{run_id}:{row['iteration']}:{self.gates[-1].gate_id}"
                        ),
                    )
                    if approved
                    else TaskFailed(reason="human approval rejected")
                ),
            ),
            self.orchestrator,
        )

    async def cancel(self, run_id: str, actor: str) -> str:
        if actor not in self.local_approvers:
            raise PermissionError("local actor cannot cancel")
        if run_id in self._active:
            return await self._cancel_owned(run_id, actor)
        lock = LocalLock(self.broker.db.path, f"engineering:{run_id}")
        try:
            if not lock.try_acquire():
                raise RuntimeError("engineering run is active in another controller")
            return await self._cancel_owned(run_id, actor)
        finally:
            lock.close()

    async def _cancel_owned(self, run_id: str, actor: str) -> str:
        row = self._row(run_id)
        if row["status"] not in {"RUNNING", "WAITING_APPROVAL"}:
            raise ValueError("engineering run cannot be cancelled")
        with self.broker.db.connect() as db:
            db.execute(
                "UPDATE engineering_runs SET status='CANCELLED',error=? WHERE run_id=?",
                (f"cancelled by {actor}", run_id),
            )
        if row["status"] == "WAITING_APPROVAL":
            engine = self._engine(Path(str(row["worktree_path"])))
            engine.cancel(f"{run_id}-approval")
        else:
            number = int(row["iteration"])
            for stage in ("implement", "review"):
                engine = self._engine(Path(str(row["worktree_path"])))
                workflow_id = f"{run_id}-i{number}-{stage}"
                try:
                    step = engine.repository.steps(workflow_id)[stage]
                except KeyError:
                    continue
                if step.execution_id and step.status.value == "RUNNING":
                    await self.manager.cancel(step.execution_id)
        self.broker.publish(
            Message(
                protocol="ACP",
                version="0.1",
                message_id=f"cancelled:{run_id}",
                run_id=run_id,
                task_id=str(row["task_id"]),
                sender=self.orchestrator.agent_id,
                recipient=self.quality_gate.agent_id,
                type=MessageType.TASK_FAILED,
                iteration=int(row["iteration"]),
                created_at=datetime.now(timezone.utc),
                payload=TaskFailed(reason=f"cancelled by {actor}"),
            ),
            self.orchestrator,
        )
        return "CANCELLED"

    def _final_snapshot_valid(self, row: dict[str, str | int]) -> bool:
        run_id = str(row["run_id"])
        number = int(row["iteration"])
        head = str(row["head_commit"])
        for path in (
            Path(str(row["worktree_path"])),
            self.worktree_root / f"{run_id}-qa-{number}",
        ):
            try:
                if commit_hash(path) != head or changed_files(path):
                    return False
            except (OSError, RuntimeError, ValueError):
                return False
        return True

    def history(self, run_id: str) -> dict[str, Any]:
        row = self._row(run_id)
        with self.broker.db.connect() as db:
            iterations = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM engineering_iterations WHERE run_id=? "
                    "ORDER BY number",
                    (run_id,),
                ).fetchall()
            ]
        messages = self.broker.get_history(run_id)
        for number in range(1, int(row["iteration"]) + 1):
            for stage in ("implement", "review"):
                messages.extend(self.broker.get_history(f"{run_id}-i{number}-{stage}"))
        messages.extend(self.broker.get_history(f"{run_id}-approval"))
        messages.sort(key=lambda message: (message.created_at, message.message_id))
        return {
            "run": row,
            "iterations": iterations,
            "messages": [message.model_dump(mode="json") for message in messages],
        }

    def report(self, run_id: str) -> dict[str, Any]:
        history = self.history(run_id)
        row = history["run"]
        iterations = history["iterations"]
        return {
            "task_id": row["task_id"],
            "specification": row["specification"],
            "criteria": json.loads(row["criteria"]),
            "status": row["status"],
            "base_commit": row["base_commit"],
            "result_commit": row["head_commit"],
            "diff": git(
                Path(row["worktree_path"]),
                "diff",
                row["base_commit"],
                row["head_commit"],
            ),
            "iterations": [
                {
                    "number": item["number"],
                    "implementation": json.loads(item["implementation"])
                    if item["implementation"]
                    else None,
                    "review": json.loads(item["review"]) if item["review"] else None,
                    "gates": json.loads(item["gates"]) if item["gates"] else None,
                }
                for item in iterations
            ],
        }
