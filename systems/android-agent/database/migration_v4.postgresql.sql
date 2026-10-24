CREATE TABLE account_users (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'SUSPENDED', 'REVOKED')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

INSERT INTO account_users(id, account_id, state, revision, created_at, updated_at)
SELECT 'user:' || id, id, 'ACTIVE', 1,
       EXTRACT(EPOCH FROM clock_timestamp())::BIGINT,
       EXTRACT(EPOCH FROM clock_timestamp())::BIGINT
FROM accounts;

CREATE TABLE tenants (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'SUSPENDED', 'RETIRED')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

CREATE TABLE merchants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    display_name TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'PAUSED', 'REVOKED')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(tenant_id, id)
);

CREATE TABLE memberships (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    merchant_id TEXT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    user_id TEXT NOT NULL REFERENCES account_users(id),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('tenant', 'merchant')),
    role TEXT NOT NULL CHECK (role IN ('owner', 'administrator', 'member')),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'SUSPENDED', 'REVOKED')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    FOREIGN KEY(tenant_id, merchant_id) REFERENCES merchants(tenant_id, id),
    CHECK (
        (scope_kind = 'tenant' AND merchant_id IS NULL) OR
        (scope_kind = 'merchant' AND merchant_id IS NOT NULL)
    ),
    UNIQUE(tenant_id, id)
);

CREATE UNIQUE INDEX idx_memberships_one_active_tenant_per_account
ON memberships(account_id) WHERE scope_kind = 'tenant' AND state = 'ACTIVE';

CREATE UNIQUE INDEX idx_memberships_active_scope
ON memberships(tenant_id, account_id, scope_kind, COALESCE(merchant_id, ''))
WHERE state = 'ACTIVE';

CREATE TABLE platform_shops (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    provider_shop_id TEXT,
    provider_shop_ref TEXT,
    identity_quality TEXT NOT NULL CHECK (identity_quality IN ('strong', 'medium', 'weak', 'unknown')),
    display_name TEXT NOT NULL,
    identity_state TEXT NOT NULL CHECK (identity_state IN ('PENDING', 'VERIFIED', 'MISMATCH')),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'PAUSED', 'REVOKED')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    FOREIGN KEY(tenant_id, merchant_id) REFERENCES merchants(tenant_id, id),
    CHECK (identity_quality <> 'strong' OR provider_shop_id IS NOT NULL),
    CHECK (identity_quality = 'unknown' OR provider_shop_id IS NOT NULL OR provider_shop_ref IS NOT NULL),
    UNIQUE(tenant_id, merchant_id, platform, id)
);

CREATE UNIQUE INDEX idx_platform_shops_provider_id
ON platform_shops(tenant_id, platform, provider_shop_id)
WHERE provider_shop_id IS NOT NULL;

CREATE TABLE platform_principals (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    provider_principal_id TEXT,
    provider_principal_ref TEXT,
    identity_quality TEXT NOT NULL CHECK (identity_quality IN ('strong', 'medium', 'weak', 'unknown')),
    display_name TEXT NOT NULL,
    role_note TEXT,
    identity_state TEXT NOT NULL CHECK (identity_state IN ('PENDING', 'VERIFIED', 'MISMATCH')),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'PAUSED', 'REVOKED')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    FOREIGN KEY(tenant_id, merchant_id) REFERENCES merchants(tenant_id, id),
    CHECK (identity_quality <> 'strong' OR provider_principal_id IS NOT NULL),
    CHECK (identity_quality = 'unknown' OR provider_principal_id IS NOT NULL OR provider_principal_ref IS NOT NULL),
    UNIQUE(tenant_id, merchant_id, platform, id)
);

CREATE UNIQUE INDEX idx_platform_principals_provider_id
ON platform_principals(tenant_id, platform, provider_principal_id)
WHERE provider_principal_id IS NOT NULL;

CREATE TABLE platform_principal_shops (
    tenant_id TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(tenant_id, principal_id, shop_id),
    FOREIGN KEY(tenant_id, merchant_id, platform, principal_id)
        REFERENCES platform_principals(tenant_id, merchant_id, platform, id),
    FOREIGN KEY(tenant_id, merchant_id, platform, shop_id)
        REFERENCES platform_shops(tenant_id, merchant_id, platform, id),
    UNIQUE(tenant_id, merchant_id, platform, principal_id, shop_id)
);

CREATE TABLE connector_enrollment_authorizations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    user_id TEXT NOT NULL REFERENCES account_users(id),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    merchant_id TEXT,
    connector_installation_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    public_key_spki_sha256 TEXT NOT NULL,
    public_key_spki BYTEA NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose = 'connector_registration'),
    state TEXT NOT NULL CHECK (state IN ('AUTHORIZED', 'CONSUMED', 'EXPIRED', 'REVOKED')),
    issued_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    consumed_at BIGINT,
    connector_id TEXT,
    registration_epoch BIGINT,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    FOREIGN KEY(tenant_id, merchant_id) REFERENCES merchants(tenant_id, id),
    CHECK (expires_at > issued_at),
    CHECK (
        (state = 'CONSUMED' AND consumed_at IS NOT NULL AND connector_id IS NOT NULL AND registration_epoch IS NOT NULL) OR
        (state <> 'CONSUMED' AND consumed_at IS NULL AND connector_id IS NULL AND registration_epoch IS NULL)
    ),
    UNIQUE(tenant_id, id)
);

CREATE TABLE connector_registration_challenges (
    id TEXT PRIMARY KEY,
    authorization_id TEXT NOT NULL REFERENCES connector_enrollment_authorizations(id),
    connector_installation_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    public_key_spki_sha256 TEXT NOT NULL,
    nonce TEXT NOT NULL,
    issued_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ISSUED', 'CONSUMED', 'EXPIRED')),
    challenge_request_id TEXT NOT NULL,
    challenge_idempotency_key TEXT NOT NULL,
    challenge_request_sha256 TEXT NOT NULL,
    challenge_json TEXT NOT NULL,
    consumed_by_command_id TEXT,
    consumed_command_sha256 TEXT,
    consumed_at BIGINT,
    accepted_json TEXT,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    CHECK (expires_at > issued_at),
    CHECK (
        (state = 'CONSUMED' AND consumed_by_command_id IS NOT NULL AND consumed_command_sha256 IS NOT NULL AND consumed_at IS NOT NULL AND accepted_json IS NOT NULL) OR
        (state <> 'CONSUMED' AND consumed_by_command_id IS NULL AND consumed_command_sha256 IS NULL AND consumed_at IS NULL AND accepted_json IS NULL)
    ),
    UNIQUE(authorization_id, challenge_request_id),
    UNIQUE(authorization_id, challenge_idempotency_key)
);

CREATE TABLE web_connectors (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    connector_installation_id TEXT NOT NULL UNIQUE,
    enrollment_authorization_id TEXT NOT NULL UNIQUE REFERENCES connector_enrollment_authorizations(id),
    registered_account_id TEXT NOT NULL REFERENCES accounts(id),
    registered_by_user_id TEXT NOT NULL REFERENCES account_users(id),
    registered_key_id TEXT NOT NULL,
    registered_public_key_spki_sha256 TEXT NOT NULL UNIQUE,
    registered_public_key_spki BYTEA NOT NULL,
    registration_epoch BIGINT NOT NULL CHECK (registration_epoch >= 1),
    device_generation BIGINT NOT NULL CHECK (device_generation >= 1),
    runtime_kind TEXT NOT NULL CHECK (runtime_kind = 'windows_web'),
    agent_version TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    identity_state TEXT NOT NULL CHECK (identity_state IN ('PENDING', 'VERIFIED', 'MISMATCH')),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'PAUSED', 'REVOKED')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(tenant_id, id)
);

CREATE TABLE connector_profiles (
    id TEXT NOT NULL,
    profile_generation BIGINT NOT NULL CHECK (profile_generation >= 1),
    tenant_id TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    runtime_kind TEXT NOT NULL CHECK (runtime_kind = 'windows_web'),
    state TEXT NOT NULL CHECK (state IN ('CREATED', 'ACTIVE', 'PAUSED', 'STOPPED', 'AUTH_REQUIRED', 'REVOKED', 'DELETED')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(id, profile_generation),
    FOREIGN KEY(tenant_id, connector_id) REFERENCES web_connectors(tenant_id, id),
    UNIQUE(tenant_id, connector_id, id, profile_generation),
    UNIQUE(tenant_id, connector_id, id, profile_generation, runtime_kind)
);

CREATE UNIQUE INDEX idx_connector_profiles_current
ON connector_profiles(tenant_id, connector_id, id)
WHERE state NOT IN ('REVOKED', 'DELETED');

CREATE TABLE platform_account_bindings (
    id TEXT NOT NULL,
    binding_generation BIGINT NOT NULL CHECK (binding_generation >= 1),
    tenant_id TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    profile_generation BIGINT NOT NULL CHECK (profile_generation >= 1),
    runtime_kind TEXT NOT NULL CHECK (runtime_kind = 'windows_web'),
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    identity_state TEXT NOT NULL CHECK (identity_state IN ('PENDING', 'VERIFIED', 'MISMATCH')),
    verified_identity_revision BIGINT,
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'ACTIVE', 'PAUSED', 'UNBOUND', 'REVOKED')),
    bound_at BIGINT NOT NULL,
    unbound_at BIGINT,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(id, binding_generation),
    FOREIGN KEY(tenant_id, merchant_id, platform, principal_id, shop_id)
        REFERENCES platform_principal_shops(tenant_id, merchant_id, platform, principal_id, shop_id),
    FOREIGN KEY(tenant_id, connector_id, profile_id, profile_generation, runtime_kind)
        REFERENCES connector_profiles(tenant_id, connector_id, id, profile_generation, runtime_kind),
    CHECK ((state IN ('UNBOUND', 'REVOKED') AND unbound_at IS NOT NULL) OR (state NOT IN ('UNBOUND', 'REVOKED') AND unbound_at IS NULL)),
    CHECK (identity_state = 'VERIFIED' OR verified_identity_revision IS NULL),
    UNIQUE(tenant_id, id, binding_generation)
);

CREATE UNIQUE INDEX idx_platform_account_bindings_current_profile
ON platform_account_bindings(tenant_id, connector_id, profile_id)
WHERE state IN ('PENDING', 'ACTIVE', 'PAUSED');

CREATE TABLE operation_idempotency (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    idempotency_key TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    actor_account_id TEXT NOT NULL REFERENCES accounts(id),
    command_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(tenant_id, idempotency_key)
);
