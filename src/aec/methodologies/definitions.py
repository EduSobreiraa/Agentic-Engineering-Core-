"""Declarative Phase 7 workflows using the existing registry contract."""

from aec.methodology.contracts import MethodologyVersion
from aec.workflows.loader import validate
from aec.workflows.models import (
    Condition,
    WorkflowDefinition,
    WorkflowLimits,
    WorkflowStep,
)


def _version(
    identifier: str, name: str, steps: list[WorkflowStep], gates: frozenset[str]
) -> MethodologyVersion:
    definition = WorkflowDefinition(
        id=identifier,
        version="1.0.0",
        name=name,
        steps=steps,
        limits=WorkflowLimits(max_parallel_steps=1, max_total_steps=len(steps)),
    )
    validate(definition)
    return MethodologyVersion(
        methodology_id=identifier,
        version="1.0.0",
        definition=definition,
        mandatory_gates=gates,
    )


TDD = _version(
    "tdd-adversarial",
    "TDD adversarial",
    [
        WorkflowStep(
            id="design_tests",
            type="agent",
            agent="test_designer",
            executor="codex",
            sandbox_mode="workspace-write",
            inputs={"prompt": "Write acceptance tests; preserve existing tests."},
        ),
        WorkflowStep(
            id="red",
            type="validation",
            validator="tdd_red",
            depends_on=["design_tests"],
        ),
        WorkflowStep(
            id="implement",
            type="agent",
            agent="implementer",
            executor="codex",
            sandbox_mode="workspace-write",
            depends_on=["red"],
            condition=Condition(step="red", result_equals=False),
            inputs={"prompt": "Implement the requirement; preserve required tests."},
        ),
        WorkflowStep(
            id="green",
            type="validation",
            validator="tdd_green",
            depends_on=["implement"],
        ),
        WorkflowStep(
            id="refactor",
            type="agent",
            agent="refactorer",
            executor="codex",
            sandbox_mode="workspace-write",
            depends_on=["green"],
            condition=Condition(step="green", result_equals=True),
            inputs={"prompt": "Refactor while preserving the required test set."},
        ),
        WorkflowStep(
            id="regression",
            type="validation",
            validator="tdd_regression",
            depends_on=["refactor"],
        ),
        WorkflowStep(
            id="qa",
            type="agent",
            agent="qa",
            executor="codex",
            depends_on=["regression"],
            condition=Condition(step="regression", result_equals=True),
            inputs={
                "prompt": "Review the result for gaps beyond required tests.",
                "artifact_id": "tdd-snapshot",
            },
        ),
        WorkflowStep(
            id="qa_gate",
            type="validation",
            validator="tdd_qa",
            depends_on=["qa"],
            inputs={"review": "{{qa.output}}"},
        ),
        WorkflowStep(
            id="approval",
            type="human_approval",
            depends_on=["qa_gate"],
            condition=Condition(step="qa_gate", result_equals=True),
        ),
    ],
    frozenset({"tdd_red", "tdd_green", "tdd_regression", "tdd_qa"}),
)

_tdd_v101_definition = TDD.definition.model_copy(deep=True)
_tdd_v101_definition.version = "1.0.1"
for _step in _tdd_v101_definition.steps:
    if _step.id == "qa":
        _step.inputs["prompt"] = (
            "Independently review the implementation and required tests at the "
            "current Git HEAD. Red and required-test integrity are checked by "
            "separate deterministic gates; assess behavior beyond those gates. "
            "Return only JSON, without Markdown, in this shape: "
            '{"task_id":"tdd","iteration":1,"reviewed_commit":"<HEAD>",'
            '"review_status":"PASSED","findings":[],"tests_executed":[],'
            '"limitations":[]}. Replace <HEAD> with the exact Git HEAD. '
            "Each finding, if any, must be a structured object, never a string. "
            "Use INCONCLUSIVE if your review is insufficient."
        )
validate(_tdd_v101_definition)
TDD_V101 = MethodologyVersion(
    methodology_id=TDD.methodology_id,
    version="1.0.1",
    definition=_tdd_v101_definition,
    mandatory_gates=TDD.mandatory_gates,
)

SECURITY_AUDIT = _version(
    "security-audit",
    "Static security audit",
    [
        WorkflowStep(id="authorize_scope", type="validation", validator="audit_scope"),
        WorkflowStep(
            id="threat_model",
            type="agent",
            agent="threat_modeler",
            executor="codex",
            depends_on=["authorize_scope"],
            condition=Condition(step="authorize_scope", result_equals=True),
            inputs={
                "prompt": "Document assets, trust boundaries and threats in scope."
            },
        ),
        WorkflowStep(
            id="static_analysis",
            type="validation",
            validator="audit_static",
            depends_on=["threat_model"],
        ),
        WorkflowStep(
            id="red_review",
            type="agent",
            agent="red_reviewer",
            executor="codex",
            depends_on=["static_analysis"],
            condition=Condition(step="static_analysis", result_equals=True),
            inputs={
                "prompt": "Review static evidence only. Do not execute exploits.",
                "artifact_id": "audit-fixture",
            },
        ),
        WorkflowStep(
            id="blue_proposal",
            type="agent",
            agent="blue_proposer",
            executor="codex",
            depends_on=["red_review"],
            inputs={"prompt": "Propose mitigations. Do not edit or execute code."},
        ),
        WorkflowStep(
            id="verify_evidence",
            type="validation",
            validator="audit_evidence",
            depends_on=["blue_proposal"],
            inputs={"proposal": "{{blue_proposal.output}}"},
        ),
        WorkflowStep(
            id="approval",
            type="human_approval",
            depends_on=["verify_evidence"],
            condition=Condition(step="verify_evidence", result_equals=True),
        ),
    ],
    frozenset({"audit_scope", "audit_static", "audit_evidence"}),
)

_audit_v101_definition = SECURITY_AUDIT.definition.model_copy(deep=True)
_audit_v101_definition.version = "1.0.1"
for _step in _audit_v101_definition.steps:
    if _step.id == "red_review":
        _step.inputs["prompt"] = (
            "Inspect only static evidence in the authorized local fixture. "
            "Do not execute code or exploits. Return only JSON with exactly "
            'the keys "severity" (low, medium, high, or critical) and '
            '"description" (nonempty finding with file and reason).'
        )
    if _step.id == "blue_proposal":
        _step.inputs["prompt"] = (
            "Propose a mitigation for this static finding: "
            "{{red_review.output}}. Do not edit or execute code."
        )
validate(_audit_v101_definition)
SECURITY_AUDIT_V101 = MethodologyVersion(
    methodology_id=SECURITY_AUDIT.methodology_id,
    version="1.0.1",
    definition=_audit_v101_definition,
    mandatory_gates=SECURITY_AUDIT.mandatory_gates,
)

_audit_v102_definition = SECURITY_AUDIT_V101.definition.model_copy(deep=True)
_audit_v102_definition.version = "1.0.2"
_audit_v102_definition.steps.insert(
    -1,
    WorkflowStep(
        id="blue_fix",
        type="agent",
        agent="blue_corrector",
        executor="codex",
        sandbox_mode="workspace-write",
        depends_on=["verify_evidence", "red_review", "blue_proposal"],
        condition=Condition(step="verify_evidence", result_equals=True),
        inputs={
            "prompt": (
                "Apply the authorized mitigation only to the local fixture. "
                "Static finding: {{red_review.output}}. "
                "Mitigation: {{blue_proposal.output}}. "
                "Do not execute the vulnerable code, exploits, or external targets."
            )
        },
    ),
)
_audit_v102_definition.steps.insert(
    -1,
    WorkflowStep(
        id="retest",
        type="validation",
        validator="audit_retest",
        depends_on=["blue_fix"],
    ),
)
_audit_v102_definition.steps[-1].depends_on = ["retest"]
_audit_v102_definition.steps[-1].condition = Condition(
    step="retest", result_equals=True
)
_audit_v102_definition.limits.max_total_steps = len(_audit_v102_definition.steps)
validate(_audit_v102_definition)
SECURITY_AUDIT_V102 = MethodologyVersion(
    methodology_id=SECURITY_AUDIT.methodology_id,
    version="1.0.2",
    definition=_audit_v102_definition,
    mandatory_gates=SECURITY_AUDIT.mandatory_gates | {"audit_retest"},
)

_audit_v103_definition = SECURITY_AUDIT_V102.definition.model_copy(deep=True)
_audit_v103_definition.version = "1.0.3"
_audit_v103_definition.metadata["approval_binding"] = "audit_artifact"
validate(_audit_v103_definition)
SECURITY_AUDIT_V103 = MethodologyVersion(
    methodology_id=SECURITY_AUDIT.methodology_id,
    version="1.0.3",
    definition=_audit_v103_definition,
    mandatory_gates=SECURITY_AUDIT_V102.mandatory_gates,
)

DECISION = _version(
    "decision-adversarial",
    "Adversarial technical decision",
    [
        WorkflowStep(
            id="proposal",
            type="agent",
            agent="proponent",
            executor="codex",
            inputs={"prompt": "State alternatives, facts, assumptions and sources."},
        ),
        WorkflowStep(
            id="contest",
            type="agent",
            agent="contestant",
            executor="codex",
            depends_on=["proposal"],
            inputs={
                "prompt": "Challenge this proposal: {{proposal.output}}",
                "artifact_id": "decision-proposal",
            },
        ),
        WorkflowStep(
            id="evaluation",
            type="agent",
            agent="evaluator",
            executor="codex",
            depends_on=["proposal", "contest"],
            inputs={
                "prompt": (
                    "Compare evidence and uncertainty; allow inconclusive. "
                    "Proposal: {{proposal.output}} Contest: {{contest.output}}"
                )
            },
        ),
        WorkflowStep(
            id="evidence_gate",
            type="validation",
            validator="decision_evidence",
            depends_on=["evaluation"],
            inputs={"evaluation": "{{evaluation.output}}"},
        ),
        WorkflowStep(
            id="approval",
            type="human_approval",
            depends_on=["evidence_gate"],
            condition=Condition(step="evidence_gate", result_equals=True),
        ),
    ],
    frozenset({"decision_evidence"}),
)

_decision_v101_definition = DECISION.definition.model_copy(deep=True)
_decision_v101_definition.version = "1.0.1"
for _step in _decision_v101_definition.steps:
    if _step.id == "contest":
        _step.inputs["prompt"] = (
            "Challenge this proposal with a concrete counterargument: "
            "{{proposal.output}}. Return only JSON with exactly the keys "
            '"severity" (low, medium, high, or critical) and '
            '"description" (nonempty critique).'
        )
validate(_decision_v101_definition)
DECISION_V101 = MethodologyVersion(
    methodology_id=DECISION.methodology_id,
    version="1.0.1",
    definition=_decision_v101_definition,
    mandatory_gates=DECISION.mandatory_gates,
)

ARENA = MethodologyVersion(
    methodology_id="implementation-arena",
    version="1.0.0",
    definition=WorkflowDefinition(
        id="implementation-arena",
        version="1.0.0",
        name="Sequential Implementation Arena selection",
        steps=[
            WorkflowStep(id="selection", type="validation", validator="arena_selected"),
            WorkflowStep(
                id="approval",
                type="human_approval",
                depends_on=["selection"],
                condition=Condition(step="selection", result_equals=True),
            ),
        ],
        metadata={"controller": "sequential-arena-v1"},
    ),
    mandatory_gates=frozenset({"arena_selected"}),
)

_arena_v101_definition = ARENA.definition.model_copy(deep=True)
_arena_v101_definition.version = "1.0.1"
_arena_v101_definition.metadata["controller"] = "sequential-arena-v2"
validate(_arena_v101_definition)
ARENA_V101 = MethodologyVersion(
    methodology_id=ARENA.methodology_id,
    version="1.0.1",
    definition=_arena_v101_definition,
    mandatory_gates=ARENA.mandatory_gates,
)

BUILTIN_METHODOLOGIES = (TDD, SECURITY_AUDIT, DECISION, ARENA)
