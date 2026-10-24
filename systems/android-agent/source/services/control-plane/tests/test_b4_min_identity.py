from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import oin_control_plane.operations_v1 as operations_v1_module
from conftest import account, controller_headers
from oin_control_plane.operations_v1 import (
    OperationsAuthentication,
    OperationsConflict,
    OperationsNotFound,
)
from oin_control_plane.security import (
    P256_ORDER,
    b64url_decode,
    b64url_encode,
    canonical_jcs_bytes,
    p256_key_id,
    load_p256_public_key,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _p1363_low_s_signature(
    private_key: ec.EllipticCurvePrivateKey, message: bytes
) -> str:
    encoded = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(encoded)
    if s > P256_ORDER // 2:
        s = P256_ORDER - s
    return b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _capabilities() -> dict:
    return {
        "max_registered_profiles": 1,
        "max_active_profiles": 1,
        "durable_inbox": True,
        "durable_outbox": True,
        "persistent_ack": True,
        "runtime_kinds": ["windows_web"],
        "wire_limits": {
            "max_frame_bytes": 262_144,
            "max_payload_bytes": 131_072,
            "max_batch_items": 1,
            "max_unacked_deliveries": 256,
            "max_unacked_bytes": 16_777_216,
        },
        "queue_limits": {
            "high_watermark_records": 10_000,
            "hard_limit_records": 20_000,
            "high_watermark_bytes": 268_435_456,
            "hard_limit_bytes": 536_870_912,
        },
    }


def _enrollment_reference(enrollment: dict) -> dict:
    return {
        "authorization_id": enrollment["authorization_id"],
        "authorization_revision": enrollment["meta"]["revision"],
        "account_id": enrollment["account_id"],
        "user_id": enrollment["user_id"],
        "tenant_id": enrollment["tenant_id"],
        "merchant_id": enrollment["merchant_id"],
        "connector_installation_id": enrollment["connector_installation_id"],
        "key_id": enrollment["key_id"],
        "public_key_spki_sha256": enrollment["public_key_spki_sha256"],
        "purpose": enrollment["purpose"],
        "expires_at": enrollment["expires_at"],
    }


def _registration_request(
    enrollment: dict,
    challenge_message: dict,
    private_key: ec.EllipticCurvePrivateKey,
    *,
    command_id: str,
    idempotency_key: str,
) -> dict:
    installation_id = enrollment["connector_installation_id"]
    public_key_spki = b64url_encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key = load_p256_public_key(public_key_spki)
    unsigned = {
        "contract": {"name": "oin.runtime", "version": 2},
        "envelope": {
            "command_id": command_id,
            "idempotency_key": idempotency_key,
            "command_type": "runtime.registration.request",
            "issued_at": _now(),
            "producer": {"actor_kind": "connector", "actor_id": installation_id},
        },
        "wire_direction": "connector_to_control_plane",
        "connector_installation_id": installation_id,
        "runtime_kind": "windows_web",
        "agent_version": "0.1.0",
        "operating_system": {
            "family": "windows",
            "version": "11.24H2",
            "architecture": "x64",
        },
        "installation_public_key": {
            "algorithm": "ECDSA_P256_SHA256",
            "key_id": p256_key_id(public_key),
            "public_key_spki": public_key_spki,
            "public_key_spki_sha256": public_key.fingerprint,
        },
        "enrollment": _enrollment_reference(enrollment),
        "capabilities": _capabilities(),
    }
    signed = b"OIN-RUNTIME-V2-REGISTRATION\n" + canonical_jcs_bytes(
        {"challenge": challenge_message["challenge"], "request": unsigned}
    )
    request = copy.deepcopy(unsigned)
    request["proof"] = {
        "algorithm": "ECDSA_P256_SHA256",
        "key_id": p256_key_id(public_key),
        "challenge_id": challenge_message["challenge"]["challenge_id"],
        "signed_payload_sha256": hashlib.sha256(signed).hexdigest(),
        "signature": _p1363_low_s_signature(private_key, signed),
    }
    return request


def _route_activation_request(
    activation_code: str,
    installation_id: str,
    private_key: ec.EllipticCurvePrivateKey,
    *,
    version: int = 2,
) -> dict:
    public_key_spki = b64url_encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key = load_p256_public_key(public_key_spki)
    decoded_code = b64url_decode(activation_code, max_bytes=40)
    unsigned = {
        "activation_code": activation_code,
        "connector_installation_id": installation_id,
        "contract": {"name": "oin.route_activation", "version": version},
        "issued_at": int.from_bytes(decoded_code[:8], "big"),
        "nonce": b64url_encode(
            hashlib.sha256(
                f"OIN-WEB-ROUTE-ACTIVATION-NONCE-V{version}\0".encode("ascii")
                + activation_code.encode("ascii")
            ).digest()[:24]
        ),
    }
    signed = (
        f"OIN-WEB-ROUTE-ACTIVATION-V{version}\n".encode("ascii")
        + canonical_jcs_bytes(unsigned)
    )
    return {
        **unsigned,
        "proof": {
            "algorithm": "ECDSA_P256_SHA256",
            "key_id": p256_key_id(public_key),
            "signed_payload_sha256": hashlib.sha256(signed).hexdigest(),
            "signature": _p1363_low_s_signature(private_key, signed),
        },
    }


def test_b4_min_sqlite_store_vertical_route(app, client) -> None:
    owner = account(client, "b4-owner")
    actor = owner["account"]
    identity = app.state.identity_store

    bootstrap = identity.bootstrap_workspace(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.bootstrap.001",
            "tenant_id": "tenant_seafood",
            "tenant_owner_membership_id": "membership_tenant_owner",
            "merchant_id": "merchant_seafood",
            "merchant_display_name": "Seafood merchant",
            "merchant_owner_membership_id": "membership_merchant_owner",
        },
    )
    assert bootstrap.replayed is False

    shop = identity.create_shop(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.shop.001",
            "tenant_id": "tenant_seafood",
            "merchant_id": "merchant_seafood",
            "shop_id": "shop_pdd_seafood",
            "platform": "pinduoduo",
            "display_name": "PDD seafood shop",
            "provider_identity": {
                "provider_platform_shop_id": "pdd-shop-10001",
                "provider_platform_shop_ref": None,
                "identity_quality": "strong",
            },
        },
    )
    assert shop.value["shop"]["identity_state"] == "PENDING"

    principal = identity.create_principal(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.principal.001",
            "tenant_id": "tenant_seafood",
            "merchant_id": "merchant_seafood",
            "principal_id": "principal_pdd_customer_service",
            "platform": "pinduoduo",
            "display_name": "PDD customer-service account",
            "role_note": "customer_service",
            "shop_ids": ["shop_pdd_seafood"],
            "provider_identity": {
                "provider_platform_principal_id": "pdd-principal-20001",
                "provider_platform_principal_ref": None,
                "identity_quality": "strong",
            },
        },
    )
    assert principal.value["principal"]["shop_ids"] == ["shop_pdd_seafood"]

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
            "idempotency_key": "idem.enrollment.001",
            "authorization_id": "enrollment_windows_001",
            "tenant_id": "tenant_seafood",
            "merchant_id": "merchant_seafood",
            "connector_installation_id": "installation_windows_001",
            "public_key_spki": public_key_spki,
        },
    ).value["enrollment"]
    assert enrollment["state"] == "AUTHORIZED"

    enrollment_reference = {
        "authorization_id": enrollment["authorization_id"],
        "authorization_revision": enrollment["meta"]["revision"],
        "account_id": enrollment["account_id"],
        "user_id": enrollment["user_id"],
        "tenant_id": enrollment["tenant_id"],
        "merchant_id": enrollment["merchant_id"],
        "connector_installation_id": enrollment["connector_installation_id"],
        "key_id": enrollment["key_id"],
        "public_key_spki_sha256": enrollment["public_key_spki_sha256"],
        "purpose": enrollment["purpose"],
        "expires_at": enrollment["expires_at"],
    }
    challenge_request = {
        "contract": {"name": "oin.runtime", "version": 2},
        "envelope": {
            "command_id": "command_registration_challenge_001",
            "idempotency_key": "idem.registration.challenge.001",
            "command_type": "runtime.registration.challenge.request",
            "issued_at": _now(),
            "producer": {
                "actor_kind": "connector",
                "actor_id": "installation_windows_001",
            },
        },
        "wire_direction": "connector_to_control_plane",
        "connector_installation_id": "installation_windows_001",
        "agent_version": "0.1.0",
        "enrollment": enrollment_reference,
    }
    challenge_message = identity.create_registration_challenge(challenge_request)
    assert identity.create_registration_challenge(challenge_request) == challenge_message

    public_key = load_p256_public_key(public_key_spki)
    unsigned_registration = {
        "contract": {"name": "oin.runtime", "version": 2},
        "envelope": {
            "command_id": "command_runtime_register_001",
            "idempotency_key": "idem.runtime.register.001",
            "command_type": "runtime.registration.request",
            "issued_at": _now(),
            "producer": {
                "actor_kind": "connector",
                "actor_id": "installation_windows_001",
            },
        },
        "wire_direction": "connector_to_control_plane",
        "connector_installation_id": "installation_windows_001",
        "runtime_kind": "windows_web",
        "agent_version": "0.1.0",
        "operating_system": {
            "family": "windows",
            "version": "11.24H2",
            "architecture": "x64",
        },
        "installation_public_key": {
            "algorithm": "ECDSA_P256_SHA256",
            "key_id": p256_key_id(public_key),
            "public_key_spki": public_key_spki,
            "public_key_spki_sha256": public_key.fingerprint,
        },
        "enrollment": enrollment_reference,
        "capabilities": _capabilities(),
    }
    signed = b"OIN-RUNTIME-V2-REGISTRATION\n" + canonical_jcs_bytes(
        {
            "challenge": challenge_message["challenge"],
            "request": unsigned_registration,
        }
    )
    registration = copy.deepcopy(unsigned_registration)
    registration["proof"] = {
        "algorithm": "ECDSA_P256_SHA256",
        "key_id": p256_key_id(public_key),
        "challenge_id": challenge_message["challenge"]["challenge_id"],
        "signed_payload_sha256": hashlib.sha256(signed).hexdigest(),
        "signature": _p1363_low_s_signature(private_key, signed),
    }
    invalid_registration = copy.deepcopy(registration)
    invalid_signature = invalid_registration["proof"]["signature"]
    invalid_registration["proof"]["signature"] = (
        ("A" if invalid_signature[0] != "A" else "B") + invalid_signature[1:]
    )
    with pytest.raises(OperationsAuthentication, match="registration_signature_invalid"):
        identity.register_connector(invalid_registration)
    with app.state.database.connect() as connection:
        assert connection.execute(
            "SELECT state FROM connector_enrollment_authorizations WHERE id = ?",
            ("enrollment_windows_001",),
        ).fetchone()["state"] == "AUTHORIZED"
        assert connection.execute(
            "SELECT state FROM connector_registration_challenges WHERE id = ?",
            (challenge_message["challenge"]["challenge_id"],),
        ).fetchone()["state"] == "ISSUED"
        assert connection.execute(
            "SELECT COUNT(*) AS value FROM web_connectors"
        ).fetchone()["value"] == 0
    accepted = identity.register_connector(registration)
    connector_id = accepted["connector_id"]
    assert identity.register_connector(registration) == accepted
    with app.state.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS value FROM web_connectors"
        ).fetchone()["value"] == 1

    profile = identity.create_profile(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.profile.001",
            "tenant_id": "tenant_seafood",
            "connector_id": connector_id,
            "profile_id": "profile_edge_001",
        },
    ).value["profile"]
    assert profile["profile_generation"] == 1

    binding = identity.create_binding(
        actor["id"],
        actor["user_id"],
        {
            "idempotency_key": "idem.binding.001",
            "tenant_id": "tenant_seafood",
            "merchant_id": "merchant_seafood",
            "shop_id": "shop_pdd_seafood",
            "platform": "pinduoduo",
            "principal_id": "principal_pdd_customer_service",
            "connector_id": connector_id,
            "profile_id": "profile_edge_001",
            "profile_generation": 1,
            "binding_id": "binding_pdd_web_001",
            "runtime_kind": "windows_web",
            "adapter_id": "pinduoduo.mms.web",
            "adapter_version": "0.1.0",
            "capabilities": ["message.observe"],
        },
    ).value["binding"]
    assert binding["binding"]["binding_generation"] == 1
    assert binding["identity_state"] == "PENDING"
    assert binding["state"] == "PENDING"

    routes = identity.list_routes(actor["id"], actor["user_id"], "tenant_seafood")
    assert [item["merchant_id"] for item in routes["merchants"]] == ["merchant_seafood"]
    assert [item["shop_id"] for item in routes["shops"]] == ["shop_pdd_seafood"]
    assert [item["principal_id"] for item in routes["principals"]] == [
        "principal_pdd_customer_service"
    ]
    assert [item["connector_id"] for item in routes["connectors"]] == [connector_id]
    assert [item["profile_id"] for item in routes["profiles"]] == ["profile_edge_001"]
    assert [item["binding"]["binding_id"] for item in routes["bindings"]] == [
        "binding_pdd_web_001"
    ]

    with pytest.raises(OperationsConflict, match="profile_capacity_reached"):
        identity.create_profile(
            actor["id"],
            actor["user_id"],
            {
                "idempotency_key": "idem.profile.second",
                "tenant_id": "tenant_seafood",
                "connector_id": connector_id,
                "profile_id": "profile_edge_002",
            },
        )

    intruder = account(client, "b4-store-intruder")
    with pytest.raises(OperationsNotFound, match="tenant_route_not_found"):
        identity.list_routes(
            intruder["account"]["id"],
            intruder["account"]["user_id"],
            "tenant_seafood",
        )
    with pytest.raises(OperationsNotFound, match="binding_route_not_found"):
        identity.create_binding(
            actor["id"],
            actor["user_id"],
            {
                "idempotency_key": "idem.binding.wrong.route",
                "tenant_id": "tenant_seafood",
                "merchant_id": "merchant_seafood",
                "shop_id": "shop_pdd_seafood",
                "platform": "pinduoduo",
                "principal_id": "principal_pdd_customer_service",
                "connector_id": connector_id,
                "profile_id": "profile_edge_missing",
                "profile_generation": 1,
                "binding_id": "binding_wrong_route",
                "runtime_kind": "windows_web",
                "adapter_id": "pinduoduo.mms.web",
                "adapter_version": "0.1.0",
                "capabilities": ["message.observe"],
            },
        )


def test_b4_min_http_bootstrap_is_csrf_scoped_idempotent_and_cross_account_hidden(
    app, client
) -> None:
    owner = account(client, "b4-http-owner")
    intruder = account(client, "b4-http-intruder")
    command = {
        "idempotency_key": "idem.http.bootstrap.001",
        "tenant_id": "tenant_http_owner",
        "tenant_owner_membership_id": "membership_http_tenant_owner",
        "merchant_id": "merchant_http_owner",
        "merchant_display_name": "HTTP merchant",
        "merchant_owner_membership_id": "membership_http_merchant_owner",
    }

    assert client.post(
        "/api/v1/operations/workspace-bootstrap", json=command
    ).status_code == 401
    assert client.post(
        "/api/v1/operations/workspace-bootstrap",
        json=command,
        headers={"Cookie": controller_headers(app, owner)["Cookie"], "X-CSRF-Token": "wrong"},
    ).status_code == 403

    owner_headers = controller_headers(app, owner)
    first = client.post(
        "/api/v1/operations/workspace-bootstrap", json=command, headers=owner_headers
    )
    assert first.status_code == 201, first.text
    assert first.json()["disposition"] == "applied"

    replay = client.post(
        "/api/v1/operations/workspace-bootstrap", json=command, headers=owner_headers
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["disposition"] == "idempotent_replay"
    changed = {**command, "merchant_display_name": "Changed merchant"}
    conflict = client.post(
        "/api/v1/operations/workspace-bootstrap", json=changed, headers=owner_headers
    )
    assert conflict.status_code == 409, conflict.text

    shop_command = {
        "idempotency_key": "idem.http.shop.001",
        "tenant_id": "tenant_http_owner",
        "merchant_id": "merchant_http_owner",
        "shop_id": "shop_http_pdd",
        "platform": "pinduoduo",
        "display_name": "HTTP PDD shop",
        "provider_identity": {
            "provider_platform_shop_id": "pdd-shop-http-1",
            "provider_platform_shop_ref": None,
            "identity_quality": "strong",
        },
    }
    created_shop = client.post(
        "/api/v1/operations/shops", json=shop_command, headers=owner_headers
    )
    assert created_shop.status_code == 201, created_shop.text
    read_shop = client.get(
        "/api/v1/operations/shops/shop_http_pdd"
        "?tenant_id=tenant_http_owner&merchant_id=merchant_http_owner",
        headers=owner_headers,
    )
    assert read_shop.status_code == 200, read_shop.text
    assert read_shop.json()["shop"]["shop_id"] == "shop_http_pdd"

    principal_command = {
        "idempotency_key": "idem.http.principal.001",
        "tenant_id": "tenant_http_owner",
        "merchant_id": "merchant_http_owner",
        "principal_id": "principal_http_pdd",
        "platform": "pinduoduo",
        "display_name": "HTTP PDD principal",
        "role_note": "customer_service",
        "shop_ids": ["shop_http_pdd"],
        "provider_identity": {
            "provider_platform_principal_id": "pdd-principal-http-1",
            "provider_platform_principal_ref": None,
            "identity_quality": "strong",
        },
    }
    created_principal = client.post(
        "/api/v1/operations/principals", json=principal_command, headers=owner_headers
    )
    assert created_principal.status_code == 201, created_principal.text
    read_principal = client.get(
        "/api/v1/operations/principals/principal_http_pdd"
        "?tenant_id=tenant_http_owner&merchant_id=merchant_http_owner",
        headers=owner_headers,
    )
    assert read_principal.status_code == 200, read_principal.text
    assert read_principal.json()["principal"]["shop_ids"] == ["shop_http_pdd"]

    hidden = client.get(
        "/api/v1/operations/routes?tenant_id=tenant_http_owner",
        headers=controller_headers(app, intruder),
    )
    assert hidden.status_code == 404, hidden.text
    hidden_write = client.post(
        "/api/v1/operations/shops",
        json={**shop_command, "idempotency_key": "idem.http.shop.intruder"},
        headers=controller_headers(app, intruder),
    )
    assert hidden_write.status_code == 404, hidden_write.text

    visible = client.get(
        "/api/v1/operations/routes?tenant_id=tenant_http_owner",
        headers=owner_headers,
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()["tenant"]["tenant_id"] == "tenant_http_owner"


def test_b4_min_http_full_vertical_route(app, client, monkeypatch) -> None:
    owner = account(client, "b4-http-vertical-owner")
    headers = controller_headers(app, owner)

    bootstrap = client.post(
        "/api/v1/operations/workspace-bootstrap",
        headers=headers,
        json={
            "idempotency_key": "idem.http.vertical.bootstrap.001",
            "tenant_id": "tenant_http_vertical",
            "tenant_owner_membership_id": "membership_http_vertical_tenant_owner",
            "merchant_id": "merchant_http_vertical",
            "merchant_display_name": "HTTP vertical merchant",
            "merchant_owner_membership_id": "membership_http_vertical_merchant_owner",
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text

    shop = client.post(
        "/api/v1/operations/shops",
        headers=headers,
        json={
            "idempotency_key": "idem.http.vertical.shop.001",
            "tenant_id": "tenant_http_vertical",
            "merchant_id": "merchant_http_vertical",
            "shop_id": "shop_http_vertical_pdd",
            "platform": "pinduoduo",
            "display_name": "HTTP vertical PDD shop",
            "provider_identity": {
                "provider_platform_shop_id": "pdd-shop-http-vertical-1",
                "provider_platform_shop_ref": None,
                "identity_quality": "strong",
            },
        },
    )
    assert shop.status_code == 201, shop.text

    principal = client.post(
        "/api/v1/operations/principals",
        headers=headers,
        json={
            "idempotency_key": "idem.http.vertical.principal.001",
            "tenant_id": "tenant_http_vertical",
            "merchant_id": "merchant_http_vertical",
            "principal_id": "principal_http_vertical_pdd",
            "platform": "pinduoduo",
            "display_name": "HTTP vertical PDD principal",
            "role_note": "customer_service",
            "shop_ids": ["shop_http_vertical_pdd"],
            "provider_identity": {
                "provider_platform_principal_id": "pdd-principal-http-vertical-1",
                "provider_platform_principal_ref": None,
                "identity_quality": "strong",
            },
        },
    )
    assert principal.status_code == 201, principal.text

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_spki = b64url_encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    enrollment_response = client.post(
        "/api/v1/operations/enrollment-authorizations",
        headers=headers,
        json={
            "idempotency_key": "idem.http.vertical.enrollment.001",
            "authorization_id": "enrollment_http_vertical_001",
            "tenant_id": "tenant_http_vertical",
            "merchant_id": "merchant_http_vertical",
            "connector_installation_id": "installation_http_vertical_001",
            "public_key_spki": public_key_spki,
        },
    )
    assert enrollment_response.status_code == 201, enrollment_response.text
    enrollment = enrollment_response.json()["enrollment"]

    challenge_request = {
        "contract": {"name": "oin.runtime", "version": 2},
        "envelope": {
            "command_id": "command_http_vertical_challenge_001",
            "idempotency_key": "idem.http.vertical.challenge.001",
            "command_type": "runtime.registration.challenge.request",
            "issued_at": _now(),
            "producer": {
                "actor_kind": "connector",
                "actor_id": "installation_http_vertical_001",
            },
        },
        "wire_direction": "connector_to_control_plane",
        "connector_installation_id": "installation_http_vertical_001",
        "agent_version": "0.1.0",
        "enrollment": _enrollment_reference(enrollment),
    }
    noncanonical = client.post(
        "/api/v2/runtime/registration-challenges",
        json=challenge_request,
    )
    assert noncanonical.status_code == 400, noncanonical.text
    assert noncanonical.content == canonical_jcs_bytes(noncanonical.json())

    challenge_response = client.post(
        "/api/v2/runtime/registration-challenges",
        content=canonical_jcs_bytes(challenge_request),
        headers={"Content-Type": "application/json"},
    )
    assert challenge_response.status_code == 201, challenge_response.text
    assert challenge_response.content == canonical_jcs_bytes(challenge_response.json())
    challenge = challenge_response.json()

    registration_request = _registration_request(
        enrollment,
        challenge,
        private_key,
        command_id="command_http_vertical_registration_001",
        idempotency_key="idem.http.vertical.registration.001",
    )
    registration_bytes = canonical_jcs_bytes(registration_request)
    registration_response = client.post(
        "/api/v2/runtime/connectors",
        content=registration_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert registration_response.status_code == 201, registration_response.text
    assert registration_response.content == canonical_jcs_bytes(
        registration_response.json()
    )
    connector_id = registration_response.json()["connector_id"]

    replay_time = operations_v1_module.now_seconds()
    with monkeypatch.context() as delayed_time:
        delayed_time.setattr(
            operations_v1_module,
            "now_seconds",
            lambda: replay_time + 301,
        )
        delayed_replay = client.post(
            "/api/v2/runtime/connectors",
            content=registration_bytes,
            headers={"Content-Type": "application/json"},
        )
        assert delayed_replay.status_code == 201, delayed_replay.text
        assert delayed_replay.content == registration_response.content

        changed_registration = copy.deepcopy(registration_request)
        changed_registration["agent_version"] = "0.1.1"
        changed_replay = client.post(
            "/api/v2/runtime/connectors",
            content=canonical_jcs_bytes(changed_registration),
            headers={"Content-Type": "application/json"},
        )
        assert changed_replay.status_code == 409, changed_replay.text

    with app.state.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS value FROM web_connectors"
        ).fetchone()["value"] == 1
        assert connection.execute(
            "SELECT state FROM connector_enrollment_authorizations WHERE id = ?",
            ("enrollment_http_vertical_001",),
        ).fetchone()["state"] == "CONSUMED"
        assert connection.execute(
            "SELECT state FROM connector_registration_challenges WHERE id = ?",
            (challenge["challenge"]["challenge_id"],),
        ).fetchone()["state"] == "CONSUMED"

    profile_id = "11111111-1111-4111-8111-111111111111"
    profile_response = client.post(
        "/api/v1/operations/profiles",
        headers=headers,
        json={
            "idempotency_key": "idem.http.vertical.profile.001",
            "tenant_id": "tenant_http_vertical",
            "connector_id": connector_id,
            "profile_id": profile_id,
        },
    )
    assert profile_response.status_code == 201, profile_response.text

    binding_response = client.post(
        "/api/v1/operations/bindings",
        headers=headers,
        json={
            "idempotency_key": "idem.http.vertical.binding.001",
            "tenant_id": "tenant_http_vertical",
            "merchant_id": "merchant_http_vertical",
            "shop_id": "shop_http_vertical_pdd",
            "platform": "pinduoduo",
            "principal_id": "principal_http_vertical_pdd",
            "connector_id": connector_id,
            "profile_id": profile_id,
            "profile_generation": 1,
            "binding_id": "binding_http_vertical_pdd_web_001",
            "runtime_kind": "windows_web",
            "adapter_id": "pinduoduo.mms.web",
            "adapter_version": "0.1.0",
            "capabilities": ["message.observe"],
        },
    )
    assert binding_response.status_code == 201, binding_response.text
    assert binding_response.json()["binding"]["identity_state"] == "PENDING"

    routes_response = client.get(
        "/api/v1/operations/routes?tenant_id=tenant_http_vertical",
        headers=headers,
    )
    assert routes_response.status_code == 200, routes_response.text
    routes = routes_response.json()
    assert [item["shop_id"] for item in routes["shops"]] == [
        "shop_http_vertical_pdd"
    ]
    assert [item["principal_id"] for item in routes["principals"]] == [
        "principal_http_vertical_pdd"
    ]
    assert [item["connector_id"] for item in routes["connectors"]] == [connector_id]
    assert [item["profile_id"] for item in routes["profiles"]] == [
        profile_id
    ]
    assert [item["binding"]["binding_id"] for item in routes["bindings"]] == [
        "binding_http_vertical_pdd_web_001"
    ]
    assert routes["bindings"][0]["identity_state"] == "PENDING"

    activation_command = {
        "tenant_id": "tenant_http_vertical",
        "connector_id": connector_id,
        "profile_id": profile_id,
        "binding_id": "binding_http_vertical_pdd_web_001",
    }
    activation_response = client.post(
        "/api/v1/operations/route-activations",
        headers=headers,
        json=activation_command,
    )
    assert activation_response.status_code == 201, activation_response.text
    activation = activation_response.json()["activation"]
    rejected_v1 = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=canonical_jcs_bytes(
            _route_activation_request(
                activation["activation_code"],
                "installation_http_vertical_001",
                private_key,
                version=1,
            )
        ),
        headers={"Content-Type": "application/json"},
    )
    assert rejected_v1.status_code == 400, rejected_v1.text
    consume_request = _route_activation_request(
        activation["activation_code"],
        "installation_http_vertical_001",
        private_key,
    )
    consume_bytes = canonical_jcs_bytes(consume_request)
    consumed = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=consume_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert consumed.status_code == 201, consumed.text
    assert consumed.content == canonical_jcs_bytes(consumed.json())
    assert consumed.json()["profile_id"] == profile_id
    assert consumed.json()["binding_generation"] == 1
    assert consumed.json()["contract"] == {
        "name": "oin.route_activation",
        "version": 2,
    }
    assert consumed.json()["target_url"] == (
        "https://mms.pinduoduo.com/chat-merchant/index.html#/"
    )
    expected_identity = consumed.json()["expected_platform_identity"]
    assert set(expected_identity) == {
        "merchant_id",
        "shop_id",
        "platform",
        "principal_id",
        "provider_platform_shop_id",
        "provider_platform_principal_id",
        "shop_revision",
        "principal_revision",
        "binding_revision",
        "binding_lifecycle_state",
        "identity_state",
        "verified_identity_revision",
    }
    assert expected_identity == {
        "merchant_id": "merchant_http_vertical",
        "shop_id": "shop_http_vertical_pdd",
        "platform": "pinduoduo",
        "principal_id": "principal_http_vertical_pdd",
        "provider_platform_shop_id": "pdd-shop-http-vertical-1",
        "provider_platform_principal_id": "pdd-principal-http-vertical-1",
        "shop_revision": 1,
        "principal_revision": 1,
        "binding_revision": 1,
        "binding_lifecycle_state": "PENDING",
        "identity_state": "PENDING",
        "verified_identity_revision": None,
    }
    assert "display_name" not in expected_identity
    assert "role_note" not in expected_identity

    replay = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=consume_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.content == consumed.content
    replay_with_new_signature = _route_activation_request(
        activation["activation_code"],
        "installation_http_vertical_001",
        private_key,
    )
    replay_with_new_signature_response = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=canonical_jcs_bytes(replay_with_new_signature),
        headers={"Content-Type": "application/json"},
    )
    assert replay_with_new_signature_response.status_code == 201
    assert replay_with_new_signature_response.content == consumed.content
    with app.state.database.connect() as connection:
        stored = connection.execute(
            """
            SELECT code_sha256, state, consumed_command_sha256, accepted_json
            FROM connector_route_activations WHERE id = ?
            """,
            (activation["activation_id"],),
        ).fetchone()
        assert stored["state"] == "CONSUMED"
        assert stored["code_sha256"] == hashlib.sha256(
            activation["activation_code"].encode("utf-8")
        ).hexdigest()
        assert activation["activation_code"] not in stored["accepted_json"]

    intruder = account(client, "b4-route-activation-intruder")
    cross_account = client.post(
        "/api/v1/operations/route-activations",
        headers=controller_headers(app, intruder),
        json=activation_command,
    )
    assert cross_account.status_code == 404, cross_account.text

    for table, identity_column, entity_id in (
        ("platform_shops", "id", "shop_http_vertical_pdd"),
        ("platform_principals", "id", "principal_http_vertical_pdd"),
        ("platform_account_bindings", "id", "binding_http_vertical_pdd_web_001"),
    ):
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                f"UPDATE {table} SET identity_state = 'MISMATCH' WHERE {identity_column} = ?",
                (entity_id,),
            )
        mismatch = client.post(
            "/api/v1/operations/route-activations",
            headers=headers,
            json=activation_command,
        )
        assert mismatch.status_code == 404, (table, mismatch.text)
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                f"UPDATE {table} SET identity_state = 'PENDING' WHERE {identity_column} = ?",
                (entity_id,),
            )

    for table, entity_id, paused_state, active_state in (
        ("merchants", "merchant_http_vertical", "PAUSED", "ACTIVE"),
        ("platform_shops", "shop_http_vertical_pdd", "PAUSED", "ACTIVE"),
        ("platform_principals", "principal_http_vertical_pdd", "PAUSED", "ACTIVE"),
        ("web_connectors", connector_id, "PAUSED", "ACTIVE"),
        ("connector_profiles", profile_id, "PAUSED", "CREATED"),
        (
            "platform_account_bindings",
            "binding_http_vertical_pdd_web_001",
            "PAUSED",
            "PENDING",
        ),
    ):
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                f"UPDATE {table} SET state = ? WHERE id = ?",
                (paused_state, entity_id),
            )
        paused = client.post(
            "/api/v1/operations/route-activations",
            headers=headers,
            json=activation_command,
        )
        assert paused.status_code == 404, (table, paused.text)
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                f"UPDATE {table} SET state = ? WHERE id = ?",
                (active_state, entity_id),
            )

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE platform_shops
            SET provider_shop_id = NULL, identity_quality = 'unknown'
            WHERE id = 'shop_http_vertical_pdd'
            """
        )
        connection.execute(
            """
            UPDATE platform_principals
            SET provider_principal_id = NULL, identity_quality = 'unknown'
            WHERE id = 'principal_http_vertical_pdd'
            """
        )
    null_provider_activation = client.post(
        "/api/v1/operations/route-activations",
        headers=headers,
        json=activation_command,
    )
    assert null_provider_activation.status_code == 201, null_provider_activation.text
    null_provider_consumed = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=canonical_jcs_bytes(
            _route_activation_request(
                null_provider_activation.json()["activation"]["activation_code"],
                "installation_http_vertical_001",
                private_key,
            )
        ),
        headers={"Content-Type": "application/json"},
    )
    assert null_provider_consumed.status_code == 201, null_provider_consumed.text
    null_expected = null_provider_consumed.json()["expected_platform_identity"]
    assert null_expected["identity_state"] == "PENDING"
    assert null_expected["provider_platform_shop_id"] is None
    assert null_expected["provider_platform_principal_id"] is None

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE platform_account_bindings
            SET identity_state = 'VERIFIED', verified_identity_revision = 2
            WHERE id = 'binding_http_vertical_pdd_web_001'
            """
        )
    invalid_verified = client.post(
        "/api/v1/operations/route-activations",
        headers=headers,
        json=activation_command,
    )
    assert invalid_verified.status_code == 409, invalid_verified.text
    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE platform_account_bindings
            SET identity_state = 'PENDING', verified_identity_revision = NULL
            WHERE id = 'binding_http_vertical_pdd_web_001'
            """
        )
        connection.execute(
            """
            UPDATE platform_shops
            SET provider_shop_id = 'pdd-shop-http-vertical-1', identity_quality = 'strong'
            WHERE id = 'shop_http_vertical_pdd'
            """
        )
        connection.execute(
            """
            UPDATE platform_principals
            SET provider_principal_id = 'pdd-principal-http-vertical-1',
                identity_quality = 'strong'
            WHERE id = 'principal_http_vertical_pdd'
            """
        )

    paused_at_consume = client.post(
        "/api/v1/operations/route-activations",
        headers=headers,
        json=activation_command,
    )
    assert paused_at_consume.status_code == 201, paused_at_consume.text
    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE platform_principals SET state = 'PAUSED' WHERE id = ?",
            ("principal_http_vertical_pdd",),
        )
    paused_consume = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=canonical_jcs_bytes(
            _route_activation_request(
                paused_at_consume.json()["activation"]["activation_code"],
                "installation_http_vertical_001",
                private_key,
            )
        ),
        headers={"Content-Type": "application/json"},
    )
    assert paused_consume.status_code == 409, paused_consume.text
    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE platform_principals SET state = 'ACTIVE' WHERE id = ?",
            ("principal_http_vertical_pdd",),
        )

    stale_activation = paused_at_consume
    with app.state.database.transaction(immediate=True) as connection:
        rotated_at = operations_v1_module.now_seconds()
        connection.execute(
            """
            UPDATE platform_account_bindings
            SET state = 'UNBOUND', unbound_at = ?, revision = revision + 1, updated_at = ?
            WHERE tenant_id = ? AND id = ? AND binding_generation = 1
            """,
            (
                rotated_at,
                rotated_at,
                "tenant_http_vertical",
                "binding_http_vertical_pdd_web_001",
            ),
        )
        connection.execute(
            """
            INSERT INTO platform_account_bindings(
                id, binding_generation, tenant_id, merchant_id, shop_id, platform,
                principal_id, connector_id, profile_id, profile_generation,
                runtime_kind, adapter_id, adapter_version, capabilities_json,
                identity_state, verified_identity_revision, state, bound_at,
                unbound_at, revision, created_at, updated_at)
            SELECT id, 2, tenant_id, merchant_id, shop_id, platform,
                   principal_id, connector_id, profile_id, profile_generation,
                   runtime_kind, adapter_id, adapter_version, capabilities_json,
                   'PENDING', NULL, 'PENDING', ?, NULL, revision + 1, created_at, ?
            FROM platform_account_bindings
            WHERE tenant_id = ? AND id = ? AND binding_generation = 1
            """,
            (
                rotated_at,
                rotated_at,
                "tenant_http_vertical",
                "binding_http_vertical_pdd_web_001",
            ),
        )
    stale_consume = client.post(
        "/api/v2/runtime/route-activations/consume",
        content=canonical_jcs_bytes(
            _route_activation_request(
                stale_activation.json()["activation"]["activation_code"],
                "installation_http_vertical_001",
                private_key,
            )
        ),
        headers={"Content-Type": "application/json"},
    )
    assert stale_consume.status_code == 409, stale_consume.text
    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            DELETE FROM platform_account_bindings
            WHERE tenant_id = ? AND id = ? AND binding_generation = 2
            """,
            ("tenant_http_vertical", "binding_http_vertical_pdd_web_001"),
        )
        connection.execute(
            """
            UPDATE platform_account_bindings
            SET state = 'PENDING', unbound_at = NULL, revision = revision + 1, updated_at = ?
            WHERE tenant_id = ? AND id = ? AND binding_generation = 1
            """,
            (
                operations_v1_module.now_seconds(),
                "tenant_http_vertical",
                "binding_http_vertical_pdd_web_001",
            ),
        )

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE merchants SET state = 'PAUSED' WHERE id = ?",
            ("merchant_http_vertical",),
        )
    paused_merchant_write = client.post(
        "/api/v1/operations/shops",
        headers=headers,
        json={
            "idempotency_key": "idem.http.vertical.paused.merchant",
            "tenant_id": "tenant_http_vertical",
            "merchant_id": "merchant_http_vertical",
            "shop_id": "shop_http_vertical_rejected",
            "platform": "pinduoduo",
            "display_name": "Must not be created",
            "provider_identity": {
                "provider_platform_shop_id": "pdd-shop-http-rejected",
                "provider_platform_shop_ref": None,
                "identity_quality": "strong",
            },
        },
    )
    assert paused_merchant_write.status_code == 404, paused_merchant_write.text
    with app.state.database.transaction(immediate=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS value FROM platform_shops WHERE id = ?",
            ("shop_http_vertical_rejected",),
        ).fetchone()["value"] == 0
        connection.execute(
            "UPDATE merchants SET state = 'ACTIVE' WHERE id = ?",
            ("merchant_http_vertical",),
        )
        connection.execute(
            "UPDATE tenants SET state = 'SUSPENDED' WHERE id = ?",
            ("tenant_http_vertical",),
        )
    suspended_tenant_route = client.get(
        "/api/v1/operations/routes?tenant_id=tenant_http_vertical",
        headers=headers,
    )
    assert suspended_tenant_route.status_code == 404, suspended_tenant_route.text
