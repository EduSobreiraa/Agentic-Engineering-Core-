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
    """
    CREATE TABLE engineering_runs (
        run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
        specification TEXT NOT NULL, criteria TEXT NOT NULL,
        allowed_paths TEXT NOT NULL, repository_path TEXT NOT NULL,
        worktree_path TEXT NOT NULL, base_commit TEXT NOT NULL,
        head_commit TEXT NOT NULL, iteration INTEGER NOT NULL DEFAULT 1,
        max_iterations INTEGER NOT NULL, max_duration_seconds REAL NOT NULL,
        gate_config TEXT NOT NULL,
        status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE engineering_iterations (
        run_id TEXT NOT NULL REFERENCES engineering_runs(run_id),
        number INTEGER NOT NULL, status TEXT NOT NULL,
        implementation TEXT, review TEXT, gates TEXT,
        PRIMARY KEY(run_id, number)
    );
    """,
    """
    CREATE TABLE documentation_records (
        document_id TEXT PRIMARY KEY,
        request_message_id TEXT NOT NULL UNIQUE REFERENCES messages(message_id),
        run_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        document_type TEXT NOT NULL,
        content TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        artifact_ref TEXT NOT NULL,
        source_refs TEXT NOT NULL,
        validation_status TEXT NOT NULL,
        warnings TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE adr_records (
        adr_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        run_id TEXT NOT NULL REFERENCES engineering_runs(run_id),
        task_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN
            ('PROPOSED','ACCEPTED','REJECTED','SUPERSEDED')),
        content TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(adr_id, revision),
        UNIQUE(adr_id, run_id, sha256)
    );
    CREATE INDEX adr_records_run ON adr_records(run_id, adr_id, revision);
    CREATE TRIGGER documentation_records_no_update
    BEFORE UPDATE ON documentation_records
    BEGIN SELECT RAISE(ABORT, 'historical document is immutable'); END;
    CREATE TRIGGER documentation_records_no_delete
    BEFORE DELETE ON documentation_records
    BEGIN SELECT RAISE(ABORT, 'historical document is immutable'); END;
    CREATE TRIGGER adr_records_no_update
    BEFORE UPDATE ON adr_records
    BEGIN SELECT RAISE(ABORT, 'ADR revision is immutable'); END;
    CREATE TRIGGER adr_records_no_delete
    BEFORE DELETE ON adr_records
    BEGIN SELECT RAISE(ABORT, 'ADR revision is immutable'); END;
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
