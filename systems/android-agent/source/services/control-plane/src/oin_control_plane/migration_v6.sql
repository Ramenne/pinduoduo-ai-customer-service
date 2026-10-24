CREATE TABLE identity_evidence (
    evidence_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    observation_sha256 TEXT NOT NULL CHECK (
        length(observation_sha256) = 64 AND observation_sha256 NOT GLOB '*[^0-9a-f]*'),
    tenant_id TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    connector_installation_id TEXT NOT NULL,
    registered_key_id TEXT NOT NULL,
    registered_public_key_spki_sha256 TEXT NOT NULL CHECK (
        length(registered_public_key_spki_sha256) = 64
        AND registered_public_key_spki_sha256 NOT GLOB '*[^0-9a-f]*'),
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK (profile_generation >= 1),
    binding_id TEXT NOT NULL,
    binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
    platform TEXT NOT NULL CHECK (platform = 'pdd'),
    adapter_id TEXT NOT NULL CHECK (adapter_id = 'pdd_web'),
    adapter_version TEXT NOT NULL,
    runtime_kind TEXT NOT NULL CHECK (runtime_kind = 'windows_web'),
    runtime_session_id TEXT NOT NULL,
    authority_fence_sha256 TEXT NOT NULL CHECK (
        length(authority_fence_sha256) = 64
        AND authority_fence_sha256 NOT GLOB '*[^0-9a-f]*'),
    page_capability TEXT NOT NULL CHECK (page_capability = 'chat'),
    source_kind TEXT NOT NULL CHECK (
        source_kind = 'pinduoduo.webview2.identity_api'),
    source_version TEXT NOT NULL CHECK (source_version = 'wv1'),
    mall_name TEXT CHECK (
        mall_name IS NULL OR (length(mall_name) BETWEEN 1 AND 128)),
    provider_shop_id TEXT NOT NULL,
    provider_principal_id TEXT NOT NULL,
    identity_quality TEXT NOT NULL CHECK (identity_quality = 'strong'),
    observed_at INTEGER NOT NULL,
    produced_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    evidence_sha256 TEXT NOT NULL CHECK (
        length(evidence_sha256) = 64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'),
    observation_state TEXT NOT NULL CHECK (observation_state = 'PENDING'),
    shop_revision INTEGER NOT NULL CHECK (shop_revision >= 1),
    principal_revision INTEGER NOT NULL CHECK (principal_revision >= 1),
    binding_revision INTEGER NOT NULL CHECK (binding_revision >= 1),
    created_at INTEGER NOT NULL,
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
    CHECK (produced_at >= observed_at AND produced_at <= expires_at),
    CHECK (expires_at > observed_at),
    UNIQUE(tenant_id, connector_id, event_id),
    UNIQUE(tenant_id, connector_id, idempotency_key)
);

CREATE INDEX idx_identity_evidence_route_expiry
ON identity_evidence(tenant_id, connector_id, profile_id, binding_id, expires_at);

INSERT INTO oin_schema_migrations(version, applied_at)
VALUES (6, CAST(strftime('%s', 'now') AS INTEGER));
