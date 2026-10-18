"""Safe YAML loading and static graph validation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import WorkflowDefinition


class WorkflowDefinitionError(ValueError):
    pass


def validate(definition: WorkflowDefinition) -> list[str]:
    if not re.fullmatch(r"1\.\d+\.\d+", definition.version):
        raise WorkflowDefinitionError("unsupported workflow version")
    steps = {step.id: step for step in definition.steps}
    if not steps or len(steps) != len(definition.steps):
        raise WorkflowDefinitionError("steps must be nonempty and have unique IDs")
    if len(steps) > definition.limits.max_total_steps:
        raise WorkflowDefinitionError("max_total_steps is smaller than the graph")
    for step in definition.steps:
        if step.type == "agent" and (
            not step.agent or not step.executor or not step.inputs.get("prompt")
        ):
            raise WorkflowDefinitionError(
                "agent requires agent, executor and inputs.prompt"
            )
        if step.type == "validation" and not step.validator:
            raise WorkflowDefinitionError("validation requires a registered validator")
        if step.type != "agent" and (step.agent or step.executor):
            raise WorkflowDefinitionError("only agent steps specify agent or executor")
        if step.type != "validation" and step.validator:
            raise WorkflowDefinitionError("only validation steps specify validator")
        if step.type == "agent" and step.retry_policy.max_attempts > 1:
            raise WorkflowDefinitionError(
                "agent retries are unsafe without reconciliation"
            )
        if step.type == "human_approval" and step.retry_policy.max_attempts > 1:
            raise WorkflowDefinitionError("approval cannot be retried")
        if len(set(step.depends_on)) != len(step.depends_on) or any(
            dep not in steps or dep == step.id for dep in step.depends_on
        ):
            raise WorkflowDefinitionError("invalid dependency")
        for value in step.inputs.values():
            for source in re.findall(r"\{\{([A-Za-z0-9_-]+)\.output\}\}", value):
                if source not in step.depends_on or steps[source].type != "agent":
                    raise WorkflowDefinitionError(
                        "input reference must name an agent dependency"
                    )
            if "{{" in re.sub(r"\{\{[A-Za-z0-9_-]+\.output\}\}", "", value):
                raise WorkflowDefinitionError("unsupported input reference")
        if step.condition:
            source = steps.get(step.condition.step)
            if (
                source is None
                or source.type != "validation"
                or source.id not in step.depends_on
            ):
                raise WorkflowDefinitionError(
                    "condition must reference a validation dependency"
                )
    pending = set(steps)
    order: list[str] = []
    while pending:
        ready = sorted(s for s in pending if set(steps[s].depends_on) <= set(order))
        if not ready:
            raise WorkflowDefinitionError("dependency cycle")
        order.extend(ready)
        pending.difference_update(ready)
    return order


def definition_hash(definition: WorkflowDefinition) -> str:
    content = json.dumps(
        definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(content.encode()).hexdigest()


def load(path: str | Path) -> WorkflowDefinition:
    file = Path(path).resolve(strict=True)
    if not file.is_file() or file.suffix not in {".yaml", ".yml"}:
        raise WorkflowDefinitionError("expected a YAML file")
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise WorkflowDefinitionError("workflow must be a mapping")
        definition = WorkflowDefinition.model_validate(data)
        validate(definition)
        return definition
    except (yaml.YAMLError, ValidationError) as exc:
        raise WorkflowDefinitionError(str(exc)) from exc
