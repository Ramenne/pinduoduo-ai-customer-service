CREATE TABLE task_delivery_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES device_tasks(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    server_session_id TEXT NOT NULL REFERENCES device_sessions(id) ON DELETE CASCADE,
    delivery_protocol INTEGER NOT NULL CHECK (delivery_protocol IN (1, 2)),
    prepared_at BIGINT NOT NULL,
    sent_at BIGINT,
    ack_deadline BIGINT,
    acknowledged_at BIGINT,
    completed_at BIGINT,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('prepared', 'sent', 'acknowledged', 'send_failed', 'ack_timeout', 'superseded')
    )
);

CREATE INDEX idx_task_delivery_attempts_task
ON task_delivery_attempts(task_id, prepared_at DESC);

CREATE INDEX idx_task_delivery_attempts_session_pending
ON task_delivery_attempts(server_session_id, outcome, ack_deadline);
