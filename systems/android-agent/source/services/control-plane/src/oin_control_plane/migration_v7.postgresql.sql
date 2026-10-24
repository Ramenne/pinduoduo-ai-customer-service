CREATE TABLE identity_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    command_idempotency_key TEXT NOT NULL,
    command_sha256 TEXT NOT NULL CHECK (
        length(command_sha256) = 64 AND command_sha256 !~ '[^0-9a-f]'),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    user_id TEXT NOT NULL REFERENCES account_users(id),
    membership_id TEXT NOT NULL,
    membership_revision BIGINT NOT NULL CHECK (membership_revision >= 1),
    evidence_sha256 TEXT NOT NULL CHECK (
        length(evidence_sha256) = 64 AND evidence_sha256 !~ '[^0-9a-f]'),
    evidence_expires_at BIGINT NOT NULL,
    shop_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    profile_generation BIGINT NOT NULL CHECK (profile_generation >= 1),
    binding_id TEXT NOT NULL,
    binding_generation BIGINT NOT NULL CHECK (binding_generation >= 1),
    platform TEXT NOT NULL CHECK (platform = 'pdd'),
    adapter_id TEXT NOT NULL CHECK (adapter_id = 'pdd_web'),
    adapter_version TEXT NOT NULL,
    runtime_kind TEXT NOT NULL CHECK (runtime_kind = 'windows_web'),
    verified_identity_revision BIGINT NOT NULL CHECK (verified_identity_revision >= 1),
    shop_revision BIGINT NOT NULL CHECK (shop_revision >= 1),
    principal_revision BIGINT NOT NULL CHECK (principal_revision >= 1),
    binding_revision BIGINT NOT NULL CHECK (binding_revision >= 1),
    result TEXT NOT NULL CHECK (result = 'CONFIRMED'),
    confirmed_at BIGINT NOT NULL,
    FOREIGN KEY(tenant_id, merchant_id) REFERENCES merchants(tenant_id, id),
    FOREIGN KEY(tenant_id, membership_id) REFERENCES memberships(tenant_id, id),
    FOREIGN KEY(evidence_id) REFERENCES identity_evidence(evidence_id),
    FOREIGN KEY(tenant_id, merchant_id, platform, shop_id)
        REFERENCES platform_shops(tenant_id, merchant_id, platform, id),
    FOREIGN KEY(tenant_id, merchant_id, platform, principal_id)
        REFERENCES platform_principals(tenant_id, merchant_id, platform, id),
    FOREIGN KEY(tenant_id, connector_id)
        REFERENCES web_connectors(tenant_id, id),
    FOREIGN KEY(tenant_id, connector_id, profile_id, profile_generation)
        REFERENCES connector_profiles(tenant_id, connector_id, id, profile_generation),
    FOREIGN KEY(tenant_id, binding_id, binding_generation)
        REFERENCES platform_account_bindings(tenant_id, id, binding_generation),
    UNIQUE(tenant_id, evidence_id),
    UNIQUE(tenant_id, command_id),
    UNIQUE(tenant_id, command_idempotency_key)
);

CREATE INDEX idx_identity_confirmations_route
ON identity_confirmations(tenant_id, connector_id, profile_id, binding_id, confirmed_at);

INSERT INTO oin_schema_migrations(version, applied_at)
VALUES (7, EXTRACT(EPOCH FROM clock_timestamp())::BIGINT)
ON CONFLICT (version) DO NOTHING;
