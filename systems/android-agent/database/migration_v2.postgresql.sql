ALTER TABLE device_tasks ADD COLUMN reported_result_json TEXT;
ALTER TABLE device_tasks ADD COLUMN result_received_at BIGINT;
ALTER TABLE device_tasks ADD COLUMN result_disposition TEXT CHECK (
    result_disposition IS NULL OR result_disposition IN (
        'on_time', 'expired_with_late_result', 'admin_revoked_with_late_result'
    )
);
ALTER TABLE device_tasks ADD COLUMN admin_disposition TEXT CHECK (
    admin_disposition IS NULL OR admin_disposition = 'admin_revoked_interrupted'
);

CREATE TABLE device_credential_repair_challenges (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    public_key_der BYTEA NOT NULL,
    public_key_sha256 TEXT NOT NULL,
    nonce TEXT,
    created_at BIGINT NOT NULL,
    approved_at BIGINT,
    expires_at BIGINT,
    used_at BIGINT
);

CREATE INDEX idx_device_credential_repair_pending
ON device_credential_repair_challenges(device_id, approved_at, used_at, created_at);

CREATE TABLE outbox_reconciliation_authorizations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    old_device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    new_device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    challenge TEXT NOT NULL,
    manifest_json TEXT,
    created_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    started_at BIGINT,
    completed_at BIGINT,
    device_acknowledged_at BIGINT,
    UNIQUE(account_id, idempotency_key),
    CHECK(old_device_id <> new_device_id)
);

CREATE INDEX idx_outbox_reconciliation_new_device
ON outbox_reconciliation_authorizations(new_device_id, completed_at, expires_at);

CREATE TABLE outbox_reconciliation_items (
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
    reconciled_at BIGINT NOT NULL,
    PRIMARY KEY(authorization_id, task_id)
);
