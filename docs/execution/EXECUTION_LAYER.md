# Execution Layer

`ExecutionManager` receives an `ExecutionRequest`, a broker-issued identity, and the ID of an ACP assignment. It verifies the assignment and role, limits concurrency to two by default, serializes turns in the same directory, persists state in the ACP SQLite database, and dispatches to the registered adapter. The adapter cannot publish ACP messages. On process success, the manager stores the final text as an artifact and publishes `ARTIFACT_SUBMITTED` or `FINDING_REPORTED` with the broker-issued identity.

The manager accepts only existing directories under explicit `allowed_roots`. One manager owns a database during live execution. Multiple independent managers sharing a database or workspace do not have a distributed lock.

An exit code of zero means that the Codex process finished and produced a final message. It does not approve the engineering work. The QA demo records a low-severity finding because severity is a deterministic choice in this phase; it does not infer severity from model text.

The manager records failures without publishing an agent-originated `TASK_FAILED`, which the ACP reserves for the orchestrator. Workflows and quality gates remain outside this layer.
