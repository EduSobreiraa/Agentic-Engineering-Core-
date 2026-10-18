# Testing the execution layer

Run the deterministic suite with `uv run pytest`. It uses a fake Codex executable and consumes no subscription quota. Run `uv run ruff check .` and `uv run mypy` for the other project gates.

To run the real two-agent demo, explicitly invoke:

```bash
uv run python -m aec.execution.demo --database /tmp/aec-phase2.sqlite --workspace /tmp/aec-phase2-work
```

The demo uses two read-only Codex turns, consumes subscription capacity, and writes AEC results and artifacts to the specified paths. Use fresh paths for an independent run. Omitting both options uses a temporary directory and removes the AEC results after display. The standard test suite does not invoke a real model turn.

To run the real scenarios in temporary directories, set `AEC_RUN_REAL_CODEX=1` for that invocation. The two-agent exchange and session-resume checks use two read-only Codex turns each. The resume check uses one non-Git directory and asserts that both turns retain the same provider session ID. Without the variable, pytest skips both cases.
