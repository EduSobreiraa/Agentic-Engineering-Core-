# Executor contract

`ExecutionRequest` identifies a turn, ACP run, task, agent, executor, prompt, working directory, sandbox mode, optional internal AEC session ID, timeout, and metadata. The manager uses the ACP assignment as the authorization source. One ACP assignment can start only one execution.

`ExecutionResult.status` is one of `CREATED`, `STARTING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `TIMED_OUT`, `CANCELLED`, or `INTERRUPTED`. Results include timestamps, the final text, exit code, artifact references, optional error code, usage, and an internal AEC session ID. Raw stdout and stderr exist in the in-memory result but are excluded from SQLite serialization.

`ExecutionAdapter` implements asynchronous `execute`, `cancel`, and `healthcheck`. A provider session ID is passed to `execute` only when the selected adapter supports resume. `AgentSession` stores separate internal and provider IDs and binds the session to one agent, executor, and directory. The manager rejects cross-agent or cross-directory reuse.
