CREATE TABLE IF NOT EXISTS oin_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    username_norm TEXT NOT NULL UNIQUE,
    email TEXT,
    email_norm TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS preauth_sessions (
    token_hash TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mobile_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE TABLE IF NOT EXISTS enrollment_challenges (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    public_key_der BLOB NOT NULL,
    public_key_sha256 TEXT NOT NULL,
    nonce TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    owner_account_id TEXT NOT NULL REFERENCES accounts(id),
    display_name TEXT NOT NULL,
    manufacturer TEXT NOT NULL,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    android_version TEXT NOT NULL,
    sdk INTEGER NOT NULL,
    app_version TEXT NOT NULL,
    app_version_code INTEGER NOT NULL,
    kernel_version TEXT NOT NULL,
    timezone TEXT NOT NULL,
    network_type TEXT NOT NULL,
    installation_id TEXT NOT NULL,
    info_json TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    binding_state TEXT NOT NULL DEFAULT 'bound' CHECK (binding_state IN ('bound', 'revoked')),
    online INTEGER NOT NULL DEFAULT 0 CHECK (online IN (0, 1)),
    last_seen_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_devices_owner ON devices(owner_account_id, created_at);

CREATE TABLE IF NOT EXISTS device_credentials (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    public_key_der BLOB NOT NULL,
    public_key_sha256 TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_active_credential_per_device
ON device_credentials(device_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS device_credential_repair_challenges (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    public_key_der BLOB NOT NULL,
    public_key_sha256 TEXT NOT NULL,
    nonce TEXT,
    created_at INTEGER NOT NULL,
    approved_at INTEGER,
    expires_at INTEGER,
    used_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_device_credential_repair_pending
ON device_credential_repair_challenges(device_id, approved_at, used_at, created_at);

CREATE TABLE IF NOT EXISTS device_revocation_challenges (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    credential_id TEXT NOT NULL REFERENCES device_credentials(id) ON DELETE CASCADE,
    nonce TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);

CREATE TABLE IF NOT EXISTS device_sessions (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    protocol_version TEXT NOT NULL,
    connected_at INTEGER NOT NULL,
    last_heartbeat_at INTEGER NOT NULL,
    disconnected_at INTEGER,
    disconnect_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_device_sessions_device ON device_sessions(device_id, connected_at);

CREATE TABLE IF NOT EXISTS device_tasks (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    requested_by_account_id TEXT NOT NULL REFERENCES accounts(id),
    task_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'dispatched', 'running', 'succeeded', 'failed', 'rejected', 'expired')),
    protocol_version TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT,
    error_message TEXT,
    reported_result_json TEXT,
    result_received_at INTEGER,
    result_disposition TEXT CHECK (
        result_disposition IS NULL OR result_disposition IN (
            'on_time', 'expired_with_late_result', 'admin_revoked_with_late_result'
        )
    ),
    admin_disposition TEXT CHECK (
        admin_disposition IS NULL OR admin_disposition = 'admin_revoked_interrupted'
    ),
    issued_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    UNIQUE(device_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_device_tasks_device ON device_tasks(device_id, issued_at DESC);

CREATE TABLE IF NOT EXISTS task_delivery_attempts (
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

CREATE INDEX IF NOT EXISTS idx_task_delivery_attempts_task
ON task_delivery_attempts(task_id, prepared_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_delivery_attempts_session_pending
ON task_delivery_attempts(server_session_id, outcome, ack_deadline);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES device_tasks(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, id);

CREATE TABLE IF NOT EXISTS outbox_reconciliation_authorizations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    old_device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    new_device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    challenge TEXT NOT NULL,
    manifest_json TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    device_acknowledged_at INTEGER,
    UNIQUE(account_id, idempotency_key),
    CHECK(old_device_id <> new_device_id)
);

CREATE INDEX IF NOT EXISTS idx_outbox_reconciliation_new_device
ON outbox_reconciliation_authorizations(new_device_id, completed_at, expires_at);

CREATE TABLE IF NOT EXISTS outbox_reconciliation_items (
    authorization_id TEXT NOT NULL REFERENCES outbox_reconciliation_authorizations(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES device_tasks(id) ON DELETE CASCADE,
    local_state TEXT NOT NULL CHECK(local_state IN ('PENDING', 'RUNNING')),
    result_sha256 TEXT NOT NULL,
    result_bytes INTEGER NOT NULL CHECK(result_bytes >= 0 AND result_bytes <= 1048576),
    disposition TEXT NOT NULL CHECK(disposition IN (
        'expired_with_late_result',
        'admin_revoked_with_late_result',
        'admin_revoked_interrupted'
    )),
    reconciled_at INTEGER NOT NULL,
    PRIMARY KEY(authorization_id, task_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT REFERENCES accounts(id),
    device_id TEXT REFERENCES devices(id),
    action TEXT NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    created_at INTEGER NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

INSERT OR IGNORE INTO oin_schema_migrations(version, applied_at)
VALUES (3, CAST(strftime('%s', 'now') AS INTEGER));
