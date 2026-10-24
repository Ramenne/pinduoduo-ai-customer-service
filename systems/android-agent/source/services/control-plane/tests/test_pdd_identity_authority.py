from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import replace
from datetime import datetime, timezone
from importlib.resources import files
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import oin_control_plane.operations_v1 as operations_v1_module
from conftest import account, controller_headers
from oin_control_plane.database import Database
from oin_control_plane.operations_v1 import (
    AuthenticatedConnectorContext,
    OperationsAuthentication,
    OperationsConflict,
    OperationsNotFound,
    PDD_IDENTITY_SOURCE_KIND,
    PDD_IDENTITY_SOURCE_VERSION,
    PDD_IDENTITY_INGRESS_CONTRACT,
    pdd_identity_authority_fence_sha256,
    pdd_identity_evidence_sha256,
    pdd_identity_ingress_signing_bytes,
)
from oin_control_plane.security import (
    P256_ORDER,
    SecurityError,
    b64url_encode,
    canonical_jcs_bytes,
)
from test_b4_min_identity import (
    _enrollment_reference,
    _now,
    _registration_request,
    _route_activation_request,
)


PROFILE_ID = "11111111-1111-4111-8111-111111111111"


def _utc(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _bootstrap(identity, actor: dict, *, suffix: str = "identity") -> None:
    identity.bootstrap_workspace(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": f"idem.{suffix}.bootstrap",
            "tenant_id": f"tenant_{suffix}",
            "tenant_owner_membership_id": f"membership_{suffix}_tenant",
            "merchant_id": f"merchant_{suffix}",
            "merchant_display_name": f"Merchant {suffix}",
            "merchant_owner_membership_id": f"membership_{suffix}_merchant",
        },
    )


def _provision_pending_route(app, client) -> dict:
    owner = account(client, "pdd-identity-owner")
    actor = owner["account"]
    identity = app.state.identity_store
    _bootstrap(identity, actor)
    identity.create_shop(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.identity.shop",
            "tenant_id": "tenant_identity",
            "merchant_id": "merchant_identity",
            "shop_id": "shop_identity",
            "platform": "pinduoduo",
            "display_name": "Pending PDD shop",
            "provider_identity": {
                "provider_platform_shop_id": None,
                "provider_platform_shop_ref": None,
                "identity_quality": "unknown",
            },
        },
    )
    identity.create_principal(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.identity.principal",
            "tenant_id": "tenant_identity",
            "merchant_id": "merchant_identity",
            "principal_id": "principal_identity",
            "platform": "pinduoduo",
            "display_name": "Pending PDD principal",
            "role_note": None,
            "shop_ids": ["shop_identity"],
            "provider_identity": {
                "provider_platform_principal_id": None,
                "provider_platform_principal_ref": None,
                "identity_quality": "unknown",
            },
        },
    )

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_spki = b64url_encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    enrollment = identity.authorize_enrollment(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.identity.enrollment",
            "authorization_id": "enrollment_identity",
            "tenant_id": "tenant_identity",
            "merchant_id": "merchant_identity",
            "connector_installation_id": "installation_identity",
            "public_key_spki": public_key_spki,
        },
    ).value["enrollment"]
    challenge = identity.create_registration_challenge(
        {
            "contract": {"name": "oin.runtime", "version": 2},
            "envelope": {
                "command_id": "command_identity_challenge",
                "idempotency_key": "idem.identity.challenge",
                "command_type": "runtime.registration.challenge.request",
                "issued_at": _now(),
                "producer": {
                    "actor_kind": "connector",
                    "actor_id": "installation_identity",
                },
            },
            "wire_direction": "connector_to_control_plane",
            "connector_installation_id": "installation_identity",
            "agent_version": "0.1.0",
            "enrollment": _enrollment_reference(enrollment),
        }
    )
    registration = _registration_request(
        enrollment,
        challenge,
        private_key,
        command_id="command_identity_registration",
        idempotency_key="idem.identity.registration",
    )
    connector_id = identity.register_connector(registration)["connector_id"]
    identity.create_profile(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.identity.profile",
            "tenant_id": "tenant_identity",
            "connector_id": connector_id,
            "profile_id": PROFILE_ID,
        },
    )
    binding = identity.create_binding(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.identity.binding",
            "tenant_id": "tenant_identity",
            "merchant_id": "merchant_identity",
            "shop_id": "shop_identity",
            "platform": "pinduoduo",
            "principal_id": "principal_identity",
            "connector_id": connector_id,
            "profile_id": PROFILE_ID,
            "profile_generation": 1,
            "binding_id": "binding_identity",
            "runtime_kind": "windows_web",
            "adapter_id": "pinduoduo.mms.web",
            "adapter_version": "0.1.0",
            "capabilities": ["message.observe"],
        },
    ).value["binding"]
    assert binding["binding"]["platform"] == "pinduoduo"
    assert binding["binding"]["adapter_id"] == "pinduoduo.mms.web"

    with app.state.database.connect() as connection:
        connector = connection.execute(
            "SELECT * FROM web_connectors WHERE tenant_id = ? AND id = ?",
            ("tenant_identity", connector_id),
        ).fetchone()
        route = connection.execute(
            """
            SELECT b.*, s.revision AS shop_revision,
                   p.revision AS principal_revision
            FROM platform_account_bindings b
            JOIN platform_shops s
              ON s.tenant_id = b.tenant_id AND s.id = b.shop_id
            JOIN platform_principals p
              ON p.tenant_id = b.tenant_id AND p.id = b.principal_id
            WHERE b.tenant_id = ? AND b.id = ?
            """,
            ("tenant_identity", "binding_identity"),
        ).fetchone()
    return {
        "owner": owner,
        "identity": identity,
        "private_key": private_key,
        "connector_id": connector_id,
        "authenticated_connector": AuthenticatedConnectorContext(
            tenant_id=connector["tenant_id"],
            connector_id=connector["id"],
            connector_installation_id=connector["connector_installation_id"],
            registered_key_id=connector["registered_key_id"],
            registered_public_key_spki_sha256=connector[
                "registered_public_key_spki_sha256"
            ],
        ),
        "route": dict(route),
    }


def _observation(provisioned: dict, *, suffix: str = "001") -> dict:
    connector_id = provisioned["connector_id"]
    route = provisioned["route"]
    current = operations_v1_module.now_seconds()
    observed_at = _utc(current)
    expires_at = _utc(current + 120)
    runtime_session_id = f"runtime_session_{suffix}"
    public_route = {
        "tenant_id": "tenant_identity",
        "merchant_id": "merchant_identity",
        "shop_id": "shop_identity",
        "principal_id": "principal_identity",
        "connector_id": connector_id,
        "profile_id": PROFILE_ID,
        "profile_generation": 1,
        "binding_id": "binding_identity",
        "binding_generation": 1,
        "platform": "pinduoduo",
        "adapter_id": "pinduoduo.mms.web",
        "adapter_version": "0.1.0",
        "runtime_kind": "windows_web",
    }
    fence = pdd_identity_authority_fence_sha256(
        route=public_route,
        authority={
            "shop_revision": int(route["shop_revision"]),
            "principal_revision": int(route["principal_revision"]),
            "binding_revision": int(route["revision"]),
            "binding_lifecycle_state": route["state"],
            "identity_state": route["identity_state"],
            "verified_identity_revision": route["verified_identity_revision"],
        },
        runtime_session_id=runtime_session_id,
    )
    observation = {
        "contract": {"name": "oin.operations", "version": 1},
        "envelope": {
            "event_id": f"event_identity_{suffix}",
            "idempotency_key": f"idem.identity.observation.{suffix}",
            "event_type": "operations.identity_observation",
            "occurred_at": observed_at,
            "produced_at": observed_at,
            "producer": {
                "actor_kind": "connector",
                "actor_id": connector_id,
            },
        },
        "evidence": {
            "evidence_id": f"evidence_identity_{suffix}",
            **{
                key: value
                for key, value in public_route.items()
                if key != "runtime_kind"
            },
            "provider_shop_identity": {
                "provider_platform_shop_id": "mall-identity-1001",
                "provider_platform_shop_ref": None,
                "identity_quality": "strong",
            },
            "provider_principal_identity": {
                "provider_platform_principal_id": "user-identity-2001",
                "provider_platform_principal_ref": None,
                "identity_quality": "strong",
            },
            "observed_at": observed_at,
            "expires_at": expires_at,
            "evidence_sha256": "0" * 64,
        },
        "admission": {
            "runtime_kind": "windows_web",
            "runtime_session_id": runtime_session_id,
            "authority_fence_sha256": fence,
            "page_capability": "chat",
            "source_kind": PDD_IDENTITY_SOURCE_KIND,
            "source_version": PDD_IDENTITY_SOURCE_VERSION,
            "mall_name": "Synthetic mall display",
        },
    }
    observation["evidence"]["evidence_sha256"] = pdd_identity_evidence_sha256(
        observation
    )
    return observation


def _refresh_evidence_digest(observation: dict) -> None:
    observation["evidence"]["evidence_sha256"] = pdd_identity_evidence_sha256(
        observation
    )


def _p1363_signature(
    private_key: ec.EllipticCurvePrivateKey,
    payload: bytes,
    *,
    force_high_s: bool = False,
) -> str:
    der = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    if force_high_s:
        s = max(s, P256_ORDER - s)
    else:
        s = min(s, P256_ORDER - s)
    return b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _identity_ingress_frame(
    provisioned: dict,
    observation: dict,
    *,
    private_key: ec.EllipticCurvePrivateKey | None = None,
    issued_at: int | None = None,
    force_high_s: bool = False,
) -> dict:
    frame = {
        "contract": {"name": PDD_IDENTITY_INGRESS_CONTRACT, "version": 1},
        "connector_installation_id": provisioned[
            "authenticated_connector"
        ].connector_installation_id,
        "issued_at": (
            operations_v1_module.now_seconds() if issued_at is None else issued_at
        ),
        "nonce": b64url_encode(
            hashlib.sha256(
                observation["evidence"]["evidence_id"].encode("utf-8")
            ).digest()[:24]
        ),
        "observation": observation,
    }
    payload = pdd_identity_ingress_signing_bytes(frame)
    frame["proof"] = {
        "algorithm": "ECDSA_P256_SHA256",
        "key_id": provisioned["authenticated_connector"].registered_key_id,
        "signed_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "signature": _p1363_signature(
            private_key or provisioned["private_key"],
            payload,
            force_high_s=force_high_s,
        ),
    }
    return frame


def _confirmation_command(
    provisioned: dict,
    observation: dict,
    *,
    suffix: str = "001",
) -> dict:
    actor = provisioned["owner"]["account"]
    return {
        "contract": {"name": "oin.operations", "version": 1},
        "envelope": {
            "command_id": f"command_identity_confirmation_{suffix}",
            "idempotency_key": f"idem.identity.confirmation.{suffix}",
            "command_type": "operations.identity.confirm",
            "issued_at": _utc(operations_v1_module.now_seconds()),
            "producer": {
                "actor_kind": "user",
                "actor_id": actor["user_id"],
                "account_id": actor["id"],
                "user_id": actor["user_id"],
            },
        },
        "confirmation_id": f"confirmation_identity_{suffix}",
        "account_id": actor["id"],
        "user_id": actor["user_id"],
        "tenant_id": "tenant_identity",
        "membership_id": "membership_identity_merchant",
        "membership_revision": 1,
        "evidence_id": observation["evidence"]["evidence_id"],
        "evidence_sha256": observation["evidence"]["evidence_sha256"],
        "expires_at": observation["evidence"]["expires_at"],
    }


def test_identity_ingress_v1_signing_vector_is_stable() -> None:
    frame = {
        "contract": {"name": PDD_IDENTITY_INGRESS_CONTRACT, "version": 1},
        "connector_installation_id": "installation_vector",
        "issued_at": 1_700_000_000,
        "nonce": "A" * 32,
        "observation": {"a": "synthetic", "z": 1},
    }
    expected = (
        b"OIN-PDD-IDENTITY-OBSERVATION-V1\n"
        b'{"connector_installation_id":"installation_vector",'
        b'"contract":{"name":"oin.pdd.identity_observation_ingress","version":1},'
        b'"issued_at":1700000000,"nonce":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",'
        b'"observation":{"a":"synthetic","z":1}}'
    )
    assert pdd_identity_ingress_signing_bytes(frame) == expected
    assert hashlib.sha256(expected).hexdigest() == (
        "e5bca72818a21fe6fcd3f5b2a239f40478f9ee806eeb71638d15620be6917457"
    )


def test_connector_authenticated_http_ingress_is_canonical_p1363_and_fail_closed(
    app, client
) -> None:
    provisioned = _provision_pending_route(app, client)
    observation = _observation(provisioned, suffix="http")
    frame = _identity_ingress_frame(provisioned, observation)
    body = canonical_jcs_bytes(frame)

    accepted = client.post(
        "/api/v2/runtime/identity-observations",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.content == canonical_jcs_bytes(accepted.json())
    assert accepted.json()["disposition"] == "applied"
    assert accepted.json()["observation"]["status"] == "PENDING"

    replay = client.post(
        "/api/v2/runtime/identity-observations",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["disposition"] == "idempotent_replay"

    noncanonical = client.post(
        "/api/v2/runtime/identity-observations",
        json=frame,
    )
    assert noncanonical.status_code == 400
    assert noncanonical.json()["error"]["detail_code"] == "runtime_json_not_canonical"

    forbidden_bearer = client.post(
        "/api/v2/runtime/identity-observations",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provisioned['owner']['token']}",
        },
    )
    assert forbidden_bearer.status_code == 400
    assert forbidden_bearer.json()["error"]["detail_code"] == (
        "identity_ingress_credential_header_forbidden"
    )

    query = client.post(
        "/api/v2/runtime/identity-observations?tenant_id=tenant_identity",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert query.status_code == 400
    assert query.json()["error"]["detail_code"] == (
        "identity_ingress_query_forbidden"
    )

    changed = copy.deepcopy(observation)
    changed["admission"]["mall_name"] = "Changed signed replay"
    _refresh_evidence_digest(changed)
    changed_response = client.post(
        "/api/v2/runtime/identity-observations",
        content=canonical_jcs_bytes(_identity_ingress_frame(provisioned, changed)),
        headers={"Content-Type": "application/json"},
    )
    assert changed_response.status_code == 409
    assert changed_response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    wrong_key = ec.generate_private_key(ec.SECP256R1())
    wrong_proof = _identity_ingress_frame(
        provisioned,
        _observation(provisioned, suffix="wrong_proof"),
        private_key=wrong_key,
    )
    wrong_proof_response = client.post(
        "/api/v2/runtime/identity-observations",
        content=canonical_jcs_bytes(wrong_proof),
        headers={"Content-Type": "application/json"},
    )
    assert wrong_proof_response.status_code == 401
    assert wrong_proof_response.json()["error"]["code"] == "PROOF_INVALID"

    high_s = _identity_ingress_frame(
        provisioned,
        _observation(provisioned, suffix="high_s"),
        force_high_s=True,
    )
    high_s_response = client.post(
        "/api/v2/runtime/identity-observations",
        content=canonical_jcs_bytes(high_s),
        headers={"Content-Type": "application/json"},
    )
    assert high_s_response.status_code == 401
    assert high_s_response.json()["error"]["detail_code"] == (
        "identity_ingress_proof_invalid"
    )

    stale = _identity_ingress_frame(
        provisioned,
        _observation(provisioned, suffix="stale_http"),
        issued_at=operations_v1_module.now_seconds() - 301,
    )
    stale_response = client.post(
        "/api/v2/runtime/identity-observations",
        content=canonical_jcs_bytes(stale),
        headers={"Content-Type": "application/json"},
    )
    assert stale_response.status_code == 401
    assert stale_response.json()["error"]["detail_code"] == (
        "identity_ingress_request_stale"
    )

    over_budget = client.post(
        "/api/v2/runtime/identity-observations",
        content=b"{" + b"x" * (16 * 1024),
        headers={"Content-Type": "application/json"},
    )
    assert over_budget.status_code == 400
    assert over_budget.json()["error"]["detail_code"] == "request_body_too_large"


def test_bearer_pending_evidence_read_is_bounded_membership_scoped_and_no_store(
    app, client
) -> None:
    provisioned = _provision_pending_route(app, client)
    observation = _observation(provisioned, suffix="pending_read")
    response = client.post(
        "/api/v2/runtime/identity-observations",
        content=canonical_jcs_bytes(
            _identity_ingress_frame(provisioned, observation)
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 201, response.text

    bearer = {"Authorization": f"Bearer {provisioned['owner']['token']}"}
    pending = client.get(
        "/api/v1/operations/identity-evidence/pending",
        headers=bearer,
    )
    assert pending.status_code == 200, pending.text
    assert pending.headers["cache-control"] == "no-store"
    assert pending.json()["limit"] == 50
    assert pending.json()["truncated"] is False
    assert len(pending.json()["items"]) == 1
    item = pending.json()["items"][0]
    assert set(item) == {
        "evidence_id",
        "evidence_sha256",
        "status",
        "tenant_id",
        "merchant_id",
        "route",
        "identity",
        "observed_at",
        "expires_at",
        "captured_revisions",
    }
    assert item["identity"] == {
        "provider_platform_shop_id": "mall-identity-1001",
        "provider_platform_principal_id": "user-identity-2001",
        "mall_name": "Synthetic mall display",
        "identity_quality": "strong",
    }
    serialized = json.dumps(item, sort_keys=True)
    for forbidden in (
        "registered_key",
        "connector_installation",
        "runtime_session",
        "authority_fence",
        "idempotency_key",
        "event_id",
    ):
        assert forbidden not in serialized

    selector = client.get(
        "/api/v1/operations/identity-evidence/pending?tenant_id=tenant_identity",
        headers=bearer,
    )
    assert selector.status_code == 400

    intruder = account(client, "pdd-pending-evidence-intruder")
    intruder_pending = client.get(
        "/api/v1/operations/identity-evidence/pending",
        headers={"Authorization": f"Bearer {intruder['token']}"},
    )
    assert intruder_pending.status_code == 200
    assert intruder_pending.json()["items"] == []

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE memberships
            SET state = 'SUSPENDED', revision = revision + 1, updated_at = updated_at + 1
            WHERE id = 'membership_identity_merchant'
            """
        )
    assert client.get(
        "/api/v1/operations/identity-evidence/pending",
        headers=bearer,
    ).json()["items"] == []


def test_bearer_confirmation_cas_verifies_and_requires_fresh_activation(
    app, client
) -> None:
    provisioned = _provision_pending_route(app, client)
    actor = provisioned["owner"]["account"]
    bearer = {"Authorization": f"Bearer {provisioned['owner']['token']}"}
    old_activation = provisioned["identity"].issue_route_activation(
        actor["id"],
        actor["user_id"],
        {
            "tenant_id": "tenant_identity",
            "connector_id": provisioned["connector_id"],
            "profile_id": PROFILE_ID,
            "binding_id": "binding_identity",
        },
    ).value["activation"]

    observation = _observation(provisioned, suffix="confirm")
    admitted = client.post(
        "/api/v2/runtime/identity-observations",
        content=canonical_jcs_bytes(
            _identity_ingress_frame(provisioned, observation)
        ),
        headers={"Content-Type": "application/json"},
    )
    assert admitted.status_code == 201, admitted.text

    command = _confirmation_command(provisioned, observation, suffix="success")
    confirmed = client.post(
        "/api/v1/operations/identity-confirmations",
        headers=bearer,
        json=command,
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.headers["cache-control"] == "no-store"
    assert confirmed.json()["disposition"] == "applied"
    assert confirmed.json()["confirmation"]["result"] == "CONFIRMED"
    assert confirmed.json()["confirmation"]["error"] is None
    authority = confirmed.json()["route_authority"]
    assert authority == {
        "tenant_id": "tenant_identity",
        "merchant_id": "merchant_identity",
        "shop_id": "shop_identity",
        "principal_id": "principal_identity",
        "connector_id": provisioned["connector_id"],
        "profile_id": PROFILE_ID,
        "profile_generation": 1,
        "binding_id": "binding_identity",
        "binding_generation": 1,
        "platform": "pinduoduo",
        "adapter_id": "pinduoduo.mms.web",
        "adapter_version": "0.1.0",
        "runtime_kind": "windows_web",
        "shop_revision": 2,
        "principal_revision": 2,
        "binding_revision": 2,
        "binding_lifecycle_state": "ACTIVE",
        "identity_state": "VERIFIED",
        "verified_identity_revision": 1,
        "fresh_route_activation_required": True,
    }
    assert "activation_code" not in json.dumps(confirmed.json(), sort_keys=True)

    with app.state.database.connect() as connection:
        states = connection.execute(
            """
            SELECT
              (SELECT identity_state FROM platform_shops WHERE id = 'shop_identity') AS shop_state,
              (SELECT provider_shop_id FROM platform_shops WHERE id = 'shop_identity') AS shop_provider,
              (SELECT revision FROM platform_shops WHERE id = 'shop_identity') AS shop_revision,
              (SELECT identity_state FROM platform_principals WHERE id = 'principal_identity') AS principal_state,
              (SELECT provider_principal_id FROM platform_principals WHERE id = 'principal_identity') AS principal_provider,
              (SELECT revision FROM platform_principals WHERE id = 'principal_identity') AS principal_revision,
              (SELECT identity_state FROM platform_account_bindings WHERE id = 'binding_identity') AS binding_state,
              (SELECT state FROM platform_account_bindings WHERE id = 'binding_identity') AS binding_lifecycle,
              (SELECT verified_identity_revision FROM platform_account_bindings WHERE id = 'binding_identity') AS verified_revision,
              (SELECT revision FROM platform_account_bindings WHERE id = 'binding_identity') AS binding_revision,
              (SELECT state FROM connector_route_activations WHERE id = ?) AS old_activation_state
            """,
            (old_activation["activation_id"],),
        ).fetchone()
        confirmation_count = connection.execute(
            "SELECT COUNT(*) AS value FROM identity_confirmations"
        ).fetchone()["value"]
        evidence_state = connection.execute(
            "SELECT observation_state FROM identity_evidence WHERE evidence_id = ?",
            (observation["evidence"]["evidence_id"],),
        ).fetchone()["observation_state"]
        audit = connection.execute(
            "SELECT detail_json FROM audit_logs WHERE action = 'pdd_identity_confirmed'"
        ).fetchone()["detail_json"]
    assert dict(states) == {
        "shop_state": "VERIFIED",
        "shop_provider": "mall-identity-1001",
        "shop_revision": 2,
        "principal_state": "VERIFIED",
        "principal_provider": "user-identity-2001",
        "principal_revision": 2,
        "binding_state": "VERIFIED",
        "binding_lifecycle": "ACTIVE",
        "verified_revision": 1,
        "binding_revision": 2,
        "old_activation_state": "REVOKED",
    }
    assert confirmation_count == 1
    assert evidence_state == "PENDING"
    assert "mall-identity-1001" not in audit
    assert "user-identity-2001" not in audit
    assert "Synthetic mall display" not in audit

    pending_after = client.get(
        "/api/v1/operations/identity-evidence/pending",
        headers=bearer,
    )
    assert pending_after.status_code == 200
    assert pending_after.json()["items"] == []

    replay = client.post(
        "/api/v1/operations/identity-confirmations",
        headers=bearer,
        json=command,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["disposition"] == "idempotent_replay"
    assert replay.json()["confirmation"]["result"] == "IDEMPOTENT_REPLAY"
    assert replay.json()["route_authority"] == authority

    variant = copy.deepcopy(command)
    variant["confirmation_id"] = "confirmation_identity_variant"
    variant_response = client.post(
        "/api/v1/operations/identity-confirmations",
        headers=bearer,
        json=variant,
    )
    assert variant_response.status_code == 409
    assert variant_response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    old_consume = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=canonical_jcs_bytes(
            _route_activation_request(
                old_activation["activation_code"],
                provisioned[
                    "authenticated_connector"
                ].connector_installation_id,
                provisioned["private_key"],
            )
        ),
        headers={"Content-Type": "application/json"},
    )
    assert old_consume.status_code == 401

    fresh_activation = client.post(
        "/api/v1/operations/route-activations",
        headers=bearer,
        json={
            "tenant_id": "tenant_identity",
            "connector_id": provisioned["connector_id"],
            "profile_id": PROFILE_ID,
            "binding_id": "binding_identity",
        },
    )
    assert fresh_activation.status_code == 201, fresh_activation.text
    fresh_consume = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=canonical_jcs_bytes(
            _route_activation_request(
                fresh_activation.json()["activation"]["activation_code"],
                provisioned[
                    "authenticated_connector"
                ].connector_installation_id,
                provisioned["private_key"],
            )
        ),
        headers={"Content-Type": "application/json"},
    )
    assert fresh_consume.status_code == 201, fresh_consume.text
    expected = fresh_consume.json()["expected_platform_identity"]
    assert expected["identity_state"] == "VERIFIED"
    assert expected["binding_lifecycle_state"] == "ACTIVE"
    assert expected["verified_identity_revision"] == 1
    assert expected["shop_revision"] == 2
    assert expected["principal_revision"] == 2
    assert expected["binding_revision"] == 2
    assert expected["provider_platform_shop_id"] == "mall-identity-1001"
    assert expected["provider_platform_principal_id"] == "user-identity-2001"

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE memberships
            SET state = 'REVOKED', revision = revision + 1, updated_at = updated_at + 1
            WHERE id = 'membership_identity_merchant'
            """
        )
    revoked_replay = client.post(
        "/api/v1/operations/identity-confirmations",
        headers=bearer,
        json=command,
    )
    assert revoked_replay.status_code == 404


def test_confirmation_rejections_preserve_pending_authority_without_partial_updates(
    app, client, monkeypatch
) -> None:
    provisioned = _provision_pending_route(app, client)
    observation = _observation(provisioned, suffix="rejections")
    provisioned["identity"].record_identity_observation(
        provisioned["authenticated_connector"], observation
    )
    bearer = {"Authorization": f"Bearer {provisioned['owner']['token']}"}

    stale_membership = _confirmation_command(
        provisioned, observation, suffix="stale_membership"
    )
    stale_membership["membership_revision"] = 2
    membership_response = client.post(
        "/api/v1/operations/identity-confirmations",
        headers=bearer,
        json=stale_membership,
    )
    assert membership_response.status_code == 409
    assert membership_response.json()["confirmation"]["error"] == {
        "code": "REVISION_CONFLICT",
        "retryable": False,
        "detail_code": "identity_confirmation_membership_revision_stale",
    }

    forged_actor = _confirmation_command(
        provisioned, observation, suffix="forged_actor"
    )
    forged_actor["account_id"] = "account_forged"
    forged_actor_response = client.post(
        "/api/v1/operations/identity-confirmations",
        headers=bearer,
        json=forged_actor,
    )
    assert forged_actor_response.status_code == 403
    assert forged_actor_response.json()["error"]["detail_code"] == (
        "identity_confirmation_actor_mismatch"
    )

    intruder = account(client, "pdd-confirmation-intruder")
    cross_tenant = _confirmation_command(
        provisioned, observation, suffix="cross_tenant"
    )
    cross_tenant["account_id"] = intruder["account"]["id"]
    cross_tenant["user_id"] = intruder["account"]["user_id"]
    cross_tenant["envelope"]["producer"] = {
        "actor_kind": "user",
        "actor_id": intruder["account"]["user_id"],
        "account_id": intruder["account"]["id"],
        "user_id": intruder["account"]["user_id"],
    }
    cross_response = client.post(
        "/api/v1/operations/identity-confirmations",
        headers={"Authorization": f"Bearer {intruder['token']}"},
        json=cross_tenant,
    )
    assert cross_response.status_code == 404

    expired = _confirmation_command(provisioned, observation, suffix="expired")
    observed_now = operations_v1_module.now_seconds()
    with monkeypatch.context() as expired_clock:
        expired_clock.setattr(
            operations_v1_module,
            "now_seconds",
            lambda: observed_now + 121,
        )
        expired_response = client.post(
            "/api/v1/operations/identity-confirmations",
            headers=bearer,
            json=expired,
        )
    assert expired_response.status_code == 409
    assert expired_response.json()["confirmation"]["error"]["code"] == (
        "IDENTITY_EVIDENCE_EXPIRED"
    )

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE platform_shops
            SET provider_shop_id = 'mall-other-without-cas', identity_quality = 'strong'
            WHERE id = 'shop_identity'
            """
        )
    provider_conflict = _confirmation_command(
        provisioned, observation, suffix="provider_conflict"
    )
    provider_conflict_response = client.post(
        "/api/v1/operations/identity-confirmations",
        headers=bearer,
        json=provider_conflict,
    )
    assert provider_conflict_response.status_code == 409
    assert provider_conflict_response.json()["confirmation"]["error"]["code"] == (
        "IDENTITY_MISMATCH"
    )

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE platform_shops
            SET provider_shop_id = NULL, identity_quality = 'unknown', revision = revision + 1
            WHERE id = 'shop_identity'
            """
        )
    stale_route = _confirmation_command(
        provisioned, observation, suffix="stale_route"
    )
    stale_route_response = client.post(
        "/api/v1/operations/identity-confirmations",
        headers=bearer,
        json=stale_route,
    )
    assert stale_route_response.status_code == 409
    assert stale_route_response.json()["confirmation"]["error"]["code"] == (
        "REVISION_CONFLICT"
    )

    with app.state.database.connect() as connection:
        states = connection.execute(
            """
            SELECT
              (SELECT identity_state FROM platform_shops WHERE id = 'shop_identity') AS shop_state,
              (SELECT identity_state FROM platform_principals WHERE id = 'principal_identity') AS principal_state,
              (SELECT identity_state FROM platform_account_bindings WHERE id = 'binding_identity') AS binding_state,
              (SELECT state FROM platform_account_bindings WHERE id = 'binding_identity') AS binding_lifecycle,
              (SELECT verified_identity_revision FROM platform_account_bindings WHERE id = 'binding_identity') AS verified_revision,
              (SELECT COUNT(*) FROM identity_confirmations) AS confirmation_count
            """
        ).fetchone()
    assert dict(states) == {
        "shop_state": "PENDING",
        "principal_state": "PENDING",
        "binding_state": "PENDING",
        "binding_lifecycle": "PENDING",
        "verified_revision": None,
        "confirmation_count": 0,
    }


def test_concurrent_confirmation_cas_allows_only_one_formal_authority(app, client) -> None:
    provisioned = _provision_pending_route(app, client)
    observation = _observation(provisioned, suffix="concurrent")
    provisioned["identity"].record_identity_observation(
        provisioned["authenticated_connector"], observation
    )
    commands = [
        _confirmation_command(provisioned, observation, suffix="concurrent_a"),
        _confirmation_command(provisioned, observation, suffix="concurrent_b"),
    ]
    actor = provisioned["owner"]["account"]
    barrier = threading.Barrier(2)

    def invoke(command: dict) -> dict:
        barrier.wait(timeout=5)
        return provisioned["identity"].confirm_identity(
            actor["id"], actor["user_id"], command
        ).value

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, commands))
    assert sorted(result["confirmation"]["result"] for result in results) == [
        "CONFIRMED",
        "REJECTED",
    ]
    with app.state.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS value FROM identity_confirmations"
        ).fetchone()["value"] == 1
        binding = connection.execute(
            """
            SELECT identity_state, state, verified_identity_revision, revision
            FROM platform_account_bindings WHERE id = 'binding_identity'
            """
        ).fetchone()
    assert dict(binding) == {
        "identity_state": "VERIFIED",
        "state": "ACTIVE",
        "verified_identity_revision": 1,
        "revision": 2,
    }


def test_bearer_authorized_context_is_active_scoped_and_non_enumerable(
    app, client
) -> None:
    owner = account(client, "pdd-context-owner")
    actor = owner["account"]
    _bootstrap(app.state.identity_store, actor, suffix="context")
    headers = {"Authorization": f"Bearer {owner['token']}"}

    response = client.get(
        "/api/v1/operations/authorized-context", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account_id"] == actor["id"]
    assert body["user_id"] == actor["user_id"]
    assert [item["membership_id"] for item in body["tenant_memberships"]] == [
        "membership_context_tenant"
    ]
    assert [item["membership_id"] for item in body["merchant_memberships"]] == [
        "membership_context_merchant"
    ]
    assert body["tenant_memberships"][0]["meta"]["revision"] == 1
    assert body["merchant_memberships"][0]["meta"]["revision"] == 1
    assert [item["tenant_id"] for item in body["tenants"]] == ["tenant_context"]
    assert [item["merchant_id"] for item in body["merchants"]] == [
        "merchant_context"
    ]
    assert response.headers["cache-control"] == "no-store"

    selector = client.get(
        "/api/v1/operations/authorized-context?tenant_id=tenant_context",
        headers=headers,
    )
    assert selector.status_code == 400

    intruder = account(client, "pdd-context-intruder")
    intruder_context = client.get(
        "/api/v1/operations/authorized-context",
        headers={"Authorization": f"Bearer {intruder['token']}"},
    )
    assert intruder_context.status_code == 200
    assert intruder_context.json()["tenant_memberships"] == []
    assert intruder_context.json()["merchant_memberships"] == []
    assert intruder_context.json()["tenants"] == []
    assert intruder_context.json()["merchants"] == []

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE memberships
            SET state = 'SUSPENDED', revision = revision + 1, updated_at = updated_at + 1
            WHERE id = 'membership_context_merchant'
            """
        )
    suspended = client.get(
        "/api/v1/operations/authorized-context", headers=headers
    ).json()
    assert suspended["tenant_memberships"]
    assert suspended["merchant_memberships"] == []
    assert suspended["merchants"] == []

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE mobile_sessions SET expires_at = 0 WHERE account_id = ?",
            (actor["id"],),
        )
    expired = client.get(
        "/api/v1/operations/authorized-context", headers=headers
    )
    assert expired.status_code == 401


def test_bearer_write_keeps_web_csrf_and_public_mapping_is_single_boundary(
    app, client
) -> None:
    owner = account(client, "pdd-public-mapping-owner")
    actor = owner["account"]
    bearer = {"Authorization": f"Bearer {owner['token']}"}
    bootstrap = client.post(
        "/api/v1/operations/workspace-bootstrap",
        headers=bearer,
        json={
            "idempotency_key": "idem.mapping.bootstrap",
            "tenant_id": "tenant_mapping",
            "tenant_owner_membership_id": "membership_mapping_tenant",
            "merchant_id": "merchant_mapping",
            "merchant_display_name": "Mapping merchant",
            "merchant_owner_membership_id": "membership_mapping_merchant",
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text

    web_headers = controller_headers(app, owner)
    cookie_only = {"Cookie": web_headers["Cookie"]}
    csrf_preserved = client.post(
        "/api/v1/operations/shops",
        headers=cookie_only,
        json={
            "idempotency_key": "idem.mapping.web.csrf",
            "tenant_id": "tenant_mapping",
            "merchant_id": "merchant_mapping",
            "shop_id": "shop_mapping_csrf",
            "platform": "pinduoduo",
            "display_name": "Must not be created",
            "provider_identity": {
                "provider_platform_shop_id": None,
                "provider_platform_shop_ref": None,
                "identity_quality": "unknown",
            },
        },
    )
    assert csrf_preserved.status_code == 403

    created = client.post(
        "/api/v1/operations/shops",
        headers=bearer,
        json={
            "idempotency_key": "idem.mapping.public.shop",
            "tenant_id": "tenant_mapping",
            "merchant_id": "merchant_mapping",
            "shop_id": "shop_mapping_public",
            "platform": "pinduoduo",
            "display_name": "Public PDD shop",
            "provider_identity": {
                "provider_platform_shop_id": None,
                "provider_platform_shop_ref": None,
                "identity_quality": "unknown",
            },
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["shop"]["platform"] == "pinduoduo"
    with app.state.database.connect() as connection:
        stored = connection.execute(
            "SELECT platform FROM platform_shops WHERE id = 'shop_mapping_public'"
        ).fetchone()
    assert stored["platform"] == "pdd"

    legacy_alias = client.post(
        "/api/v1/operations/shops",
        headers=bearer,
        json={
            "idempotency_key": "idem.mapping.legacy.shop",
            "tenant_id": "tenant_mapping",
            "merchant_id": "merchant_mapping",
            "shop_id": "shop_mapping_legacy",
            "platform": "pdd",
            "display_name": "Legacy alias rejected",
            "provider_identity": {
                "provider_platform_shop_id": None,
                "provider_platform_shop_ref": None,
                "identity_quality": "unknown",
            },
        },
    )
    assert legacy_alias.status_code == 400


def test_pending_observation_is_durable_idempotent_and_does_not_verify(
    app, client
) -> None:
    provisioned = _provision_pending_route(app, client)
    observation = _observation(provisioned)
    outcome = provisioned["identity"].record_identity_observation(
        provisioned["authenticated_connector"], observation
    )
    assert outcome.replayed is False
    assert outcome.value["observation"]["status"] == "PENDING"
    assert outcome.value["observation"]["evidence"]["platform"] == "pinduoduo"
    assert (
        outcome.value["observation"]["evidence"]["adapter_id"]
        == "pinduoduo.mms.web"
    )
    assert outcome.value["observation"]["admission"] == observation["admission"]
    assert outcome.value["observation"]["captured_revisions"] == {
        "shop_revision": 1,
        "principal_revision": 1,
        "binding_revision": 1,
    }

    replay = provisioned["identity"].record_identity_observation(
        provisioned["authenticated_connector"], copy.deepcopy(observation)
    )
    assert replay.replayed is True
    assert replay.value == outcome.value

    with app.state.database.connect() as connection:
        evidence = connection.execute(
            "SELECT * FROM identity_evidence"
        ).fetchall()
        states = connection.execute(
            """
            SELECT
              (SELECT identity_state FROM platform_shops WHERE id = 'shop_identity') AS shop_state,
              (SELECT identity_state FROM platform_principals WHERE id = 'principal_identity') AS principal_state,
              (SELECT identity_state FROM platform_account_bindings WHERE id = 'binding_identity') AS binding_state,
              (SELECT verified_identity_revision FROM platform_account_bindings WHERE id = 'binding_identity') AS verified_revision
            """
        ).fetchone()
        audit = connection.execute(
            """
            SELECT detail_json FROM audit_logs
            WHERE action = 'pdd_identity_observation_persisted'
            """
        ).fetchone()["detail_json"]
    assert len(evidence) == 1
    assert evidence[0]["platform"] == "pdd"
    assert evidence[0]["adapter_id"] == "pdd_web"
    assert evidence[0]["observation_state"] == "PENDING"
    assert states["shop_state"] == "PENDING"
    assert states["principal_state"] == "PENDING"
    assert states["binding_state"] == "PENDING"
    assert states["verified_revision"] is None
    assert "mall-identity-1001" not in audit
    assert "user-identity-2001" not in audit
    assert "Synthetic mall display" not in audit


def test_observation_parser_and_store_fail_closed_on_auth_route_and_content(
    app, client
) -> None:
    provisioned = _provision_pending_route(app, client)
    identity = provisioned["identity"]
    accepted = _observation(provisioned, suffix="accepted")
    identity.record_identity_observation(
        provisioned["authenticated_connector"], accepted
    )

    actor = provisioned["owner"]["account"]
    binding_command = {
        "idempotency_key": "idem.identity.mapping.reject",
        "tenant_id": "tenant_identity",
        "merchant_id": "merchant_identity",
        "shop_id": "shop_identity",
        "platform": "pinduoduo",
        "principal_id": "principal_identity",
        "connector_id": provisioned["connector_id"],
        "profile_id": "profile_identity",
        "profile_generation": 1,
        "binding_id": "binding_identity_alias",
        "runtime_kind": "windows_web",
        "adapter_id": "pdd_web",
        "adapter_version": "0.1.0",
        "capabilities": ["message.observe"],
    }
    with pytest.raises(SecurityError, match="adapter_id_unsupported"):
        identity.create_binding(actor["id"], actor["user_id"], binding_command)
    legacy_platform = copy.deepcopy(binding_command)
    legacy_platform["platform"] = "pdd"
    legacy_platform["adapter_id"] = "pinduoduo.mms.web"
    with pytest.raises(SecurityError, match="platform_unsupported"):
        identity.create_binding(actor["id"], actor["user_id"], legacy_platform)

    changed_replay = copy.deepcopy(accepted)
    changed_replay["admission"]["mall_name"] = "Changed replay"
    _refresh_evidence_digest(changed_replay)
    with pytest.raises(
        OperationsConflict, match="identity_observation_idempotency_reuse"
    ):
        identity.record_identity_observation(
            provisioned["authenticated_connector"], changed_replay
        )

    wrong_key = replace(
        provisioned["authenticated_connector"],
        registered_public_key_spki_sha256="f" * 64,
    )
    with pytest.raises(
        OperationsAuthentication, match="identity_observation_authentication_failed"
    ):
        identity.record_identity_observation(
            wrong_key, _observation(provisioned, suffix="wrong_key")
        )

    stale_generation = _observation(provisioned, suffix="stale_generation")
    stale_generation["evidence"]["binding_generation"] = 2
    _refresh_evidence_digest(stale_generation)
    with pytest.raises(
        OperationsNotFound, match="identity_observation_route_not_found"
    ):
        identity.record_identity_observation(
            provisioned["authenticated_connector"], stale_generation
        )

    stale_fence = _observation(provisioned, suffix="stale_fence")
    stale_fence["admission"]["authority_fence_sha256"] = "f" * 64
    _refresh_evidence_digest(stale_fence)
    with pytest.raises(
        OperationsConflict, match="identity_observation_authority_fence_stale"
    ):
        identity.record_identity_observation(
            provisioned["authenticated_connector"], stale_fence
        )

    extra_field = _observation(provisioned, suffix="extra_field")
    extra_field["admission"]["raw_page"] = "forbidden"
    with pytest.raises(SecurityError, match="identity_observation_admission_fields_invalid"):
        identity.record_identity_observation(
            provisioned["authenticated_connector"], extra_field
        )

    unsupported_source = _observation(provisioned, suffix="unsupported_source")
    unsupported_source["admission"]["source_kind"] = "dom_guess"
    _refresh_evidence_digest(unsupported_source)
    with pytest.raises(SecurityError, match="identity_source_unsupported"):
        identity.record_identity_observation(
            provisioned["authenticated_connector"], unsupported_source
        )

    tampered_identity = _observation(provisioned, suffix="tampered_identity")
    tampered_identity["evidence"]["provider_shop_identity"][
        "provider_platform_shop_id"
    ] = "mall-tampered"
    with pytest.raises(SecurityError, match="identity_evidence_digest_mismatch"):
        identity.record_identity_observation(
            provisioned["authenticated_connector"], tampered_identity
        )

    expired = _observation(provisioned, suffix="expired")
    current = operations_v1_module.now_seconds()
    expired["envelope"]["occurred_at"] = _utc(current - 240)
    expired["envelope"]["produced_at"] = _utc(current - 240)
    expired["evidence"]["observed_at"] = _utc(current - 240)
    expired["evidence"]["expires_at"] = _utc(current - 120)
    _refresh_evidence_digest(expired)
    with pytest.raises(SecurityError, match="identity_evidence_expiry_invalid"):
        identity.record_identity_observation(
            provisioned["authenticated_connector"], expired
        )

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE platform_shops
            SET provider_shop_id = 'mall-other-route', identity_quality = 'strong'
            WHERE id = 'shop_identity'
            """
        )
    conflicting_identity = _observation(
        provisioned, suffix="conflicting_identity"
    )
    with pytest.raises(
        OperationsConflict, match="identity_observation_identity_conflict"
    ):
        identity.record_identity_observation(
            provisioned["authenticated_connector"], conflicting_identity
        )


def test_v7_empty_and_v5_sqlite_migration_match_postgresql_shape(tmp_path) -> None:
    empty = Database(tmp_path / "empty.db")
    empty.migrate()
    empty.verify_schema()
    with empty.connect() as connection:
        assert connection.execute(
            "SELECT MAX(version) AS version FROM oin_schema_migrations"
        ).fetchone()["version"] == 7
        sqlite_columns = tuple(
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(identity_evidence)"
            ).fetchall()
        )

        confirmation_columns = tuple(
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(identity_confirmations)"
            ).fetchall()
        )

    v5 = Database(tmp_path / "v5.db")
    with v5.connect() as connection:
        connection.executescript(
            files("oin_control_plane").joinpath("schema.sql").read_text(
                encoding="utf-8"
            )
        )
        connection.executescript(
            files("oin_control_plane").joinpath("migration_v4.sql").read_text(
                encoding="utf-8"
            )
        )
        connection.executescript(
            files("oin_control_plane").joinpath("migration_v5.sql").read_text(
                encoding="utf-8"
            )
        )
    v5.migrate()
    v5.verify_schema()
    with v5.connect() as connection:
        assert connection.execute(
            "SELECT MAX(version) AS version FROM oin_schema_migrations"
        ).fetchone()["version"] == 7
        assert connection.execute(
            "SELECT COUNT(*) AS value FROM identity_evidence"
        ).fetchone()["value"] == 0
        assert connection.execute(
            "SELECT COUNT(*) AS value FROM identity_confirmations"
        ).fetchone()["value"] == 0

    sqlite_migration = files("oin_control_plane").joinpath(
        "migration_v6.sql"
    ).read_text(encoding="utf-8")
    postgres_migration = files("oin_control_plane").joinpath(
        "migration_v6.postgresql.sql"
    ).read_text(encoding="utf-8")
    expected_columns = (
        "evidence_id",
        "event_id",
        "idempotency_key",
        "observation_sha256",
        "tenant_id",
        "merchant_id",
        "shop_id",
        "principal_id",
        "connector_id",
        "connector_installation_id",
        "registered_key_id",
        "registered_public_key_spki_sha256",
        "profile_id",
        "profile_generation",
        "binding_id",
        "binding_generation",
        "platform",
        "adapter_id",
        "adapter_version",
        "runtime_kind",
        "runtime_session_id",
        "authority_fence_sha256",
        "page_capability",
        "source_kind",
        "source_version",
        "mall_name",
        "provider_shop_id",
        "provider_principal_id",
        "identity_quality",
        "observed_at",
        "produced_at",
        "expires_at",
        "evidence_sha256",
        "observation_state",
        "shop_revision",
        "principal_revision",
        "binding_revision",
        "created_at",
    )
    assert sqlite_columns == expected_columns
    for column in expected_columns:
        assert f"\n    {column} " in sqlite_migration
        assert f"\n    {column} " in postgres_migration
    for invariant in (
        "platform = 'pdd'",
        "adapter_id = 'pdd_web'",
        "runtime_kind = 'windows_web'",
        "source_kind = 'pinduoduo.webview2.identity_api'",
        "source_version = 'wv1'",
        "observation_state = 'PENDING'",
        "UNIQUE(tenant_id, connector_id, event_id)",
        "UNIQUE(tenant_id, connector_id, idempotency_key)",
    ):
        assert invariant in sqlite_migration
        assert invariant in postgres_migration

    sqlite_v7 = files("oin_control_plane").joinpath(
        "migration_v7.sql"
    ).read_text(encoding="utf-8")
    postgres_v7 = files("oin_control_plane").joinpath(
        "migration_v7.postgresql.sql"
    ).read_text(encoding="utf-8")
    expected_confirmation_columns = (
        "confirmation_id",
        "tenant_id",
        "merchant_id",
        "evidence_id",
        "command_id",
        "command_idempotency_key",
        "command_sha256",
        "account_id",
        "user_id",
        "membership_id",
        "membership_revision",
        "evidence_sha256",
        "evidence_expires_at",
        "shop_id",
        "principal_id",
        "connector_id",
        "profile_id",
        "profile_generation",
        "binding_id",
        "binding_generation",
        "platform",
        "adapter_id",
        "adapter_version",
        "runtime_kind",
        "verified_identity_revision",
        "shop_revision",
        "principal_revision",
        "binding_revision",
        "result",
        "confirmed_at",
    )
    assert confirmation_columns == expected_confirmation_columns
    for column in expected_confirmation_columns:
        assert f"\n    {column} " in sqlite_v7
        assert f"\n    {column} " in postgres_v7
    for invariant in (
        "platform = 'pdd'",
        "adapter_id = 'pdd_web'",
        "runtime_kind = 'windows_web'",
        "result = 'CONFIRMED'",
        "UNIQUE(tenant_id, evidence_id)",
        "UNIQUE(tenant_id, command_idempotency_key)",
    ):
        assert invariant in sqlite_v7
        assert invariant in postgres_v7
