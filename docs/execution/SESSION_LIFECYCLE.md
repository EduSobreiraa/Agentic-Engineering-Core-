# Session lifecycle

A new Codex turn begins without a session ID. A `thread.started` event may provide a provider ID. The manager creates a distinct AEC session ID and stores both IDs in `agent_sessions` with the agent, executor, and directory. A later request passes only the AEC ID; the manager checks ownership before handing the provider ID to the adapter.

At startup, the manager marks persisted `STARTING` and `RUNNING` executions `INTERRUPTED`. It does not replay them, because a prior process may have modified a workspace. A duplicate execution ID returns the saved final result; an in-progress or interrupted execution is not restarted automatically. SQLite retains execution results and ACP history. The demo uses a fresh run ID on each invocation.
