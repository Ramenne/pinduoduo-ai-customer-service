CREATE TABLE connector_route_activations (
    id TEXT PRIMARY KEY,
    code_sha256 TEXT NOT NULL UNIQUE CHECK (
        length(code_sha256) = 64 AND code_sha256 !~ '[^0-9a-f]'),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    user_id TEXT NOT NULL REFERENCES account_users(id),
    tenant_id TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    connector_installation_id TEXT NOT NULL,
    registered_key_id TEXT NOT NULL,
    registered_public_key_spki_sha256 TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK (profile_generation >= 1),
    binding_id TEXT NOT NULL,
    binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
    state TEXT NOT NULL CHECK (state IN ('ISSUED', 'CONSUMED', 'EXPIRED', 'REVOKED')),
    issued_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    consumed_at BIGINT,
    consumed_command_sha256 TEXT,
    accepted_json TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    FOREIGN KEY(tenant_id, connector_id)
        REFERENCES web_connectors(tenant_id, id),
    FOREIGN KEY(tenant_id, connector_id, profile_id, profile_generation)
        REFERENCES connector_profiles(tenant_id, connector_id, id, profile_generation),
    FOREIGN KEY(tenant_id, binding_id, binding_generation)
        REFERENCES platform_account_bindings(tenant_id, id, binding_generation),
    CHECK (expires_at > issued_at),
    CHECK (
        (state = 'CONSUMED' AND consumed_at IS NOT NULL
            AND consumed_command_sha256 IS NOT NULL AND accepted_json IS NOT NULL) OR
        (state <> 'CONSUMED' AND consumed_at IS NULL
            AND consumed_command_sha256 IS NULL AND accepted_json IS NULL)
    )
);

CREATE UNIQUE INDEX idx_connector_route_activation_issued
ON connector_route_activations(tenant_id, connector_id, profile_id)
WHERE state = 'ISSUED';

INSERT INTO oin_schema_migrations(version, applied_at)
VALUES (5, EXTRACT(EPOCH FROM clock_timestamp())::BIGINT)
ON CONFLICT (version) DO NOTHING;
