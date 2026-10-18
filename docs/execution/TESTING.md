# Testing the execution layer

Run the deterministic suite with `uv run pytest`. It uses a fake Codex executable and consumes no subscription quota. Run `uv run ruff check .` and `uv run mypy` for the other project gates.

To run the real two-agent demo, explicitly invoke:

```bash
uv run python -m aec.execution.demo --database /tmp/aec-phase2.sqlite --workspace /tmp/aec-phase2-work
```

The demo uses two read-only Codex turns, consumes subscription capacity, and writes AEC results and artifacts to the specified paths. Use fresh paths for an independent run. Omitting both options uses a temporary directory and removes the AEC results after display. The standard test suite does not invoke a real model turn. A separate two-turn read-only check on 2026-10-18 verified real session resume after correcting the adapter's non-Git workspace flag.

To run the real end-to-end pytest scenario in temporary directories, set `AEC_RUN_REAL_CODEX=1` for that invocation. It uses two Codex turns. Without that variable, pytest reports the case as skipped.
