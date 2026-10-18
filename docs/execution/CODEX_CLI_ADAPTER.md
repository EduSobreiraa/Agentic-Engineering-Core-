# Codex CLI adapter

The local CLI was inspected on 2026-10-17. `codex-cli 0.163.0` supports `exec --json`, `--sandbox read-only`, `--sandbox workspace-write`, and `exec resume`. `codex login status` reported `Logged in using ChatGPT`. No credential file was opened or changed.

`CodexCliAdapter` requires a CLI version at least 0.163 and a successful `codex login status`. It runs `codex exec` without a shell, sends the prompt on stdin, and parses JSONL events. `thread.started` provides the provider session ID, `item.completed` with `agent_message` supplies the final text, and `turn.completed` supplies usage. A malformed JSONL stream or a missing final message fails the turn.

For resume, the adapter passes the saved provider session ID to `codex exec ... resume`. The CLI stores its own session data; the AEC records IDs but never reads or modifies Codex session files. CLI failure details remain in the in-memory stderr field. The persisted error is a short code. Authentication status does not prove that a model turn will succeed or that subscription quota remains.
