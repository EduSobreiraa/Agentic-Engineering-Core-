from scripts.real_codex_benchmark import TASKS


def test_reference_tasks_have_observable_gates() -> None:
    accepted = (
        "The team used several methods to help with the migration.",
        "The team reviewed the report yesterday.",
        "We need to decide on the configuration before starting the test.",
    )
    for task, output in zip(TASKS, accepted, strict=True):
        assert task.accepts(output)
        assert not task.accepts(task.prompt)
        assert len(task.acceptance_sha256()) == 64
