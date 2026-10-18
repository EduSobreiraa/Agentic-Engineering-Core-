# Execution Layer

`ExecutionManager` receives an `ExecutionRequest`, a broker-issued identity, and the ID of an ACP assignment. It verifies the assignment and role, limits concurrency to two by default, serializes turns in the same directory, persists state in the ACP SQLite database, and dispatches to the registered adapter. The adapter cannot publish ACP messages. On process success, the manager stores the final text as an artifact and publishes `ARTIFACT_SUBMITTED` or `FINDING_REPORTED` with the broker-issued identity.

The manager accepts only existing directories under explicit `allowed_roots`. `workspace-write` also requires an agent ID in `writable_agents` and an ACP assignment with `metadata.workspace_write=true`. Local POSIX file locks bind each execution and workspace across manager processes. Reconciliation skips an execution whose lock is still held. Different workspaces may run concurrently. The adversarial methodology creates its Git worktrees before dispatch; the generic manager does not choose Git repositories or create worktrees. These locks require a shared local filesystem and do not coordinate workflow schedulers or remote hosts.

An exit code of zero means that the Codex process finished and produced a final message. It does not approve the engineering work. QA must return JSON with `severity` and `description`; the manager validates these fields before publishing a finding. Severity remains the QA agent's assessment, not a deterministic quality gate.

The manager records failures without publishing an agent-originated `TASK_FAILED`, which the ACP reserves for the orchestrator. Workflows and quality gates remain outside this layer.
