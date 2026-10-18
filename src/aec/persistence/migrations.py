"""Versioned SQLite schema."""

import sqlite3

MIGRATIONS = (
    """
    CREATE TABLE agents (
        agent_id TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE messages (
        message_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        sender TEXT NOT NULL REFERENCES agents(agent_id),
        recipient TEXT NOT NULL REFERENCES agents(agent_id),
        type TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX messages_history ON messages(run_id, created_at, message_id);
    CREATE TABLE deliveries (
        delivery_id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL UNIQUE REFERENCES messages(message_id),
        recipient TEXT NOT NULL REFERENCES agents(agent_id),
        state TEXT NOT NULL CHECK(state IN
            ('PENDING', 'CLAIMED', 'ACKNOWLEDGED', 'RETRY_PENDING', 'DEAD_LETTER')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        claim_token TEXT,
        lease_until TEXT,
        acknowledged_at TEXT,
        last_error TEXT
    );
    CREATE INDEX deliveries_ready ON deliveries(recipient, state);
    CREATE TABLE processing_receipts (
        message_id TEXT NOT NULL REFERENCES messages(message_id),
        recipient TEXT NOT NULL REFERENCES agents(agent_id),
        acknowledged_at TEXT NOT NULL,
        PRIMARY KEY(message_id, recipient)
    );
    CREATE TABLE events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id TEXT,
        agent_id TEXT,
        kind TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE executions (
        execution_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        executor TEXT NOT NULL,
        working_directory TEXT NOT NULL,
        source_message_id TEXT NOT NULL UNIQUE,
        request_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        result TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE agent_sessions (
        session_id TEXT PRIMARY KEY,
        provider_session_id TEXT NOT NULL,
        executor TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        working_directory TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE workflow_runs (
        run_id TEXT PRIMARY KEY, definition TEXT NOT NULL,
        definition_hash TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
        finished_at TEXT, error TEXT
    );
    CREATE TABLE workflow_steps (
        run_id TEXT NOT NULL REFERENCES workflow_runs(run_id), step_id TEXT NOT NULL,
        status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
        execution_id TEXT, started_at TEXT, finished_at TEXT, result TEXT,
        PRIMARY KEY(run_id, step_id)
    );
    CREATE TABLE workflow_attempts (
        run_id TEXT NOT NULL, step_id TEXT NOT NULL, attempt INTEGER NOT NULL,
        execution_id TEXT, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY(run_id, step_id, attempt),
        FOREIGN KEY(run_id, step_id) REFERENCES workflow_steps(run_id, step_id)
    );
    CREATE TABLE workflow_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
        step_id TEXT, kind TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE workflow_approvals (
        run_id TEXT NOT NULL, step_id TEXT NOT NULL, status TEXT NOT NULL,
        decided_by TEXT, decided_at TEXT, PRIMARY KEY(run_id, step_id),
        FOREIGN KEY(run_id, step_id) REFERENCES workflow_steps(run_id, step_id)
    );
    """,
)


def migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise RuntimeError("database schema is newer than this ACP version")
    for index in range(version, len(MIGRATIONS)):
        connection.executescript(
            f"BEGIN IMMEDIATE; {MIGRATIONS[index]} "
            f"PRAGMA user_version = {index + 1}; COMMIT;"
        )
