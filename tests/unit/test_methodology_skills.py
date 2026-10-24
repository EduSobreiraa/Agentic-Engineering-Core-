from pathlib import Path

import pytest

from aec.methodologies.skills import inspect_local_skill


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
