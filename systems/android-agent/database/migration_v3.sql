CREATE TABLE task_delivery_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES device_tasks(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    server_session_id TEXT NOT NULL REFERENCES device_sessions(id) ON DELETE CASCADE,
    delivery_protocol INTEGER NOT NULL CHECK (delivery_protocol IN (1, 2)),
    prepared_at INTEGER NOT NULL,
    sent_at INTEGER,
    ack_deadline INTEGER,
    acknowledged_at INTEGER,
    completed_at INTEGER,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('prepared', 'sent', 'acknowledged', 'send_failed', 'ack_timeout', 'superseded')
    )
);

CREATE INDEX idx_task_delivery_attempts_task
ON task_delivery_attempts(task_id, prepared_at DESC);

CREATE INDEX idx_task_delivery_attempts_session_pending
ON task_delivery_attempts(server_session_id, outcome, ack_deadline);

INSERT INTO oin_schema_migrations(version, applied_at)
VALUES (3, CAST(strftime('%s', 'now') AS INTEGER));
