"""Roles and broker-issued local handles."""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ORCHESTRATOR = "orchestrator"
    IMPLEMENTER = "implementer"
    QA = "qa-adversarial"
    DOCUMENTATION = "documentation-worker"
    METHODOLOGY = "methodology-worker"
    QUALITY_GATE = "quality-gate"


@dataclass(frozen=True)
class Identity:
    agent_id: str
    role: Role
    handle: str
