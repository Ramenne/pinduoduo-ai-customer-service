CREATE TABLE seller_items (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    title TEXT NOT NULL,
    data_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','published','archived')),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE TABLE seller_recipes (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('publish','edit','brighten','metrics','reply','research')),
    steps_json TEXT NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE TABLE seller_jobs (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    item_id TEXT REFERENCES seller_items(id),
    recipe_id TEXT REFERENCES seller_recipes(id),
    kind TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','waiting','unknown','succeeded','failed','cancelled')),
    step_index INTEGER NOT NULL DEFAULT 0,
    probe_attempt INTEGER NOT NULL DEFAULT 0,
    task_id TEXT REFERENCES device_tasks(id),
    error_code TEXT,
    idempotency_key TEXT NOT NULL,
    due_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(account_id,idempotency_key)
);
CREATE INDEX idx_seller_jobs_device ON seller_jobs(device_id,status,created_at);
CREATE TABLE seller_schedules (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    item_id TEXT REFERENCES seller_items(id),
    recipe_id TEXT NOT NULL REFERENCES seller_recipes(id),
    interval_seconds BIGINT NOT NULL CHECK (interval_seconds >= 3600),
    next_due_at BIGINT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    created_at BIGINT NOT NULL
);
INSERT INTO oin_schema_migrations(version,applied_at)
VALUES (8,EXTRACT(EPOCH FROM clock_timestamp())::BIGINT) ON CONFLICT(version) DO NOTHING;
