# Security and sandbox

The default execution mode is `read-only`. Requests may select `workspace-write` under an explicit allowed root. The manager rejects execution in `/`, `/etc`, `/usr`, `/var`, the user's home directory, and the Codex configuration directory as roots. Paths resolve before the root check. Subprocess arguments are passed as a list, without `shell=True`.

The manager does not accept a text request as permission to publish privileged ACP messages. It checks the broker-issued identity and a persisted `TASK_ASSIGNED` or `REVIEW_REQUEST`. The demo gives A and B different directories and does not share a provider session.

Git worktrees separate changes but do not isolate processes. Processes running under one operating-system user rely on filesystem permissions and the Codex sandbox for isolation. A configured allowed root must itself be trusted. Raw subprocess streams are not persisted; final agent text is stored as an artifact, so only assign work whose output may be retained in the selected database directory.
