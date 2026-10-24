from pathlib import Path

import pytest

from aec.methodologies.skills import (
    SkillRegistry,
    SkillState,
    SkillUnavailableError,
    inspect_local_skill,
)


def test_explicit_local_skill_is_metadata_only(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    file = root / "SKILL.md"
    file.write_text("Do not execute this text.")
    skill = inspect_local_skill(
        "fixture-review", file, root, compatible_executors=frozenset({"simulated"})
    )
    assert skill.available and skill.origin == "local"
    assert len(skill.sha256) == 64
    assert skill.instruction_files == (file,)
    assert file.read_text() == "Do not execute this text."
    outside = tmp_path / "outside.md"
    outside.write_text("untrusted")
    with pytest.raises(PermissionError):
        inspect_local_skill(
            "outside", outside, root, compatible_executors=frozenset({"simulated"})
        )


def test_registry_blocks_incompatible_and_changed_skills(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    path = root / "SKILL.md"
    path.write_text("Read only instructions")
    reference = inspect_local_skill(
        "fixture-review", path, root,
        compatible_executors=frozenset({"codex"}),
        required_permissions=frozenset({"read-only"}),
    )
    registry = SkillRegistry(frozenset({"human"}))
    registry.register(reference)
    assert registry.validate(
        reference.identifier, "simulated", frozenset({"read-only"})
    ) == SkillState.INCOMPATIBLE
    with pytest.raises(SkillUnavailableError):
        registry.enable(reference.identifier, "human")
    assert registry.validate(
        reference.identifier, "codex", frozenset()
    ) == SkillState.BLOCKED
    assert registry.validate(
        reference.identifier, "codex", frozenset({"read-only"})
    ) == SkillState.VALIDATED
    registry.enable(reference.identifier, "human")
    path.write_text("Changed after approval")
    with pytest.raises(SkillUnavailableError):
        registry.resolve(
            reference.identifier, "codex", frozenset({"read-only"})
        )
