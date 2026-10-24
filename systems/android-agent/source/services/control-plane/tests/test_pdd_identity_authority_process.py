from __future__ import annotations

import copy
import hashlib
import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from oin_control_plane.operations_v1 import (
    PDD_IDENTITY_INGRESS_CONTRACT,
    PDD_IDENTITY_SOURCE_KIND,
    PDD_IDENTITY_SOURCE_VERSION,
    pdd_identity_authority_fence_sha256,
    pdd_identity_evidence_sha256,
    pdd_identity_ingress_signing_bytes,
)
from oin_control_plane.security import (
    b64url_encode,
    canonical_jcs_bytes,
    load_p256_public_key,
    p256_key_id,
)
from test_b4_min_identity import (
    _enrollment_reference,
    _now,
    _p1363_low_s_signature,
    _registration_request,
    _route_activation_request,
)


SERVICE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SERVICE_ROOT / "src"
PROFILE_ID = "22222222-2222-4222-8222-222222222222"
TENANT_ID = "tenant_k2_process"
MERCHANT_ID = "merchant_k2_process"
SHOP_ID = "shop_k2_process"
PRINCIPAL_ID = "principal_k2_process"
BINDING_ID = "binding_k2_process"
INSTALLATION_ID = "installation_k2_process"
MEMBERSHIP_ID = "membership_k2_process_merchant"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _running_control_plane(
    database_path: Path,
    log_directory: Path,
    run_number: int,
) -> Iterator[httpx.Client]:
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("OIN_"):
            environment.pop(name)
    environment.update(
        {
            "OIN_DATABASE_PATH": str(database_path),
            "OIN_DATABASE_AUTO_MIGRATE": "true",
            "OIN_PUBLIC_BASE_URL": base_url,
            "OIN_ENVIRONMENT": "development",
            "OIN_BIND_HOST": "127.0.0.1",
            "OIN_BIND_PORT": str(port),
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    (str(SOURCE_ROOT), environment.get("PYTHONPATH", "")),
                )
            ),
        }
    )
    log_path = log_directory / f"control-plane-run-{run_number}.log"
    with log_path.open("w", encoding="utf-8") as log_stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "oin_control_plane"],
            cwd=SERVICE_ROOT,
            env=environment,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
        client = httpx.Client(
            base_url=base_url,
            timeout=10.0,
            trust_env=False,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("control-plane process did not become healthy")
            if process.poll() is not None:
                log_stream.flush()
                raise AssertionError(
                    "control-plane process exited during startup:\n"
                    + log_path.read_text(encoding="utf-8")
                )
            yield client
        finally:
            client.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _controller_post(
    client: httpx.Client,
    token: str,
    path: str,
    body: dict,
    *,
    expected_status: int = 201,
) -> dict:
    response = client.post(path, headers=_bearer(token), json=body)
    assert response.status_code == expected_status, response.text
    return response.json()


def _runtime_post(
    client: httpx.Client,
    path: str,
    body: dict,
    *,
    expected_status: int = 201,
) -> httpx.Response:
    response = client.post(
        path,
        content=canonical_jcs_bytes(body),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == expected_status, response.text
    assert response.content == canonical_jcs_bytes(response.json())
    return response


def _login(client: httpx.Client) -> tuple[dict, str]:
    response = client.post(
        "/api/v1/mobile/sessions",
        json={
            "identifier": "pdd-k2-process-owner",
            "password": "correct horse battery 2026!",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["account"], response.json()["access_token"]


def _utc(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _identity_observation(
    *,
    connector_id: str,
    expected_identity: dict,
    suffix: str,
) -> dict:
    current = int(time.time())
    observed_at = _utc(current)
    runtime_session_id = "runtime_session_k2_process_001"
    public_route = {
        "tenant_id": TENANT_ID,
        "merchant_id": MERCHANT_ID,
        "shop_id": SHOP_ID,
        "principal_id": PRINCIPAL_ID,
        "connector_id": connector_id,
        "profile_id": PROFILE_ID,
        "profile_generation": 1,
        "binding_id": BINDING_ID,
        "binding_generation": 1,
        "platform": "pinduoduo",
        "adapter_id": "pinduoduo.mms.web",
        "adapter_version": "0.1.0",
        "runtime_kind": "windows_web",
    }
    fence = pdd_identity_authority_fence_sha256(
        route=public_route,
        authority={
            "shop_revision": expected_identity["shop_revision"],
            "principal_revision": expected_identity["principal_revision"],
            "binding_revision": expected_identity["binding_revision"],
            "binding_lifecycle_state": expected_identity[
                "binding_lifecycle_state"
            ],
            "identity_state": expected_identity["identity_state"],
            "verified_identity_revision": expected_identity[
                "verified_identity_revision"
            ],
        },
        runtime_session_id=runtime_session_id,
    )
    observation = {
        "contract": {"name": "oin.operations", "version": 1},
        "envelope": {
            "event_id": f"event_k2_process_{suffix}",
            "idempotency_key": f"idem.k2.process.observation.{suffix}",
            "event_type": "operations.identity_observation",
            "occurred_at": observed_at,
            "produced_at": observed_at,
            "producer": {
                "actor_kind": "connector",
                "actor_id": connector_id,
            },
        },
        "evidence": {
            "evidence_id": f"evidence_k2_process_{suffix}",
            **{
                key: value
                for key, value in public_route.items()
                if key != "runtime_kind"
            },
            "provider_shop_identity": {
                "provider_platform_shop_id": "mall-k2-process-1001",
                "provider_platform_shop_ref": None,
                "identity_quality": "strong",
            },
            "provider_principal_identity": {
                "provider_platform_principal_id": "user-k2-process-2001",
                "provider_platform_principal_ref": None,
                "identity_quality": "strong",
            },
            "observed_at": observed_at,
            "expires_at": _utc(current + 240),
            "evidence_sha256": "0" * 64,
        },
        "admission": {
            "runtime_kind": "windows_web",
            "runtime_session_id": runtime_session_id,
            "authority_fence_sha256": fence,
            "page_capability": "chat",
            "source_kind": PDD_IDENTITY_SOURCE_KIND,
            "source_version": PDD_IDENTITY_SOURCE_VERSION,
            "mall_name": "K2 process evidence mall",
        },
    }
    observation["evidence"]["evidence_sha256"] = (
        pdd_identity_evidence_sha256(observation)
    )
    return observation


def _ingress_frame(
    observation: dict,
    private_key: ec.EllipticCurvePrivateKey,
    key_id: str,
) -> dict:
    frame = {
        "contract": {"name": PDD_IDENTITY_INGRESS_CONTRACT, "version": 1},
        "connector_installation_id": INSTALLATION_ID,
        "issued_at": int(time.time()),
        "nonce": b64url_encode(os.urandom(24)),
        "observation": observation,
    }
    signing_bytes = pdd_identity_ingress_signing_bytes(frame)
    frame["proof"] = {
        "algorithm": "ECDSA_P256_SHA256",
        "key_id": key_id,
        "signed_payload_sha256": hashlib.sha256(signing_bytes).hexdigest(),
        "signature": _p1363_low_s_signature(private_key, signing_bytes),
    }
    return frame


def _confirmation_command(
    actor: dict,
    membership_revision: int,
    observation: dict,
) -> dict:
    return {
        "contract": {"name": "oin.operations", "version": 1},
        "envelope": {
            "command_id": "command_k2_process_confirmation_001",
            "idempotency_key": "idem.k2.process.confirmation.001",
            "command_type": "operations.identity.confirm",
            "issued_at": _utc(int(time.time())),
            "producer": {
                "actor_kind": "user",
                "actor_id": actor["user_id"],
                "account_id": actor["id"],
                "user_id": actor["user_id"],
            },
        },
        "confirmation_id": "confirmation_k2_process_001",
        "account_id": actor["id"],
        "user_id": actor["user_id"],
        "tenant_id": TENANT_ID,
        "membership_id": MEMBERSHIP_ID,
        "membership_revision": membership_revision,
        "evidence_id": observation["evidence"]["evidence_id"],
        "evidence_sha256": observation["evidence"]["evidence_sha256"],
        "expires_at": observation["evidence"]["expires_at"],
    }


def test_real_process_file_sqlite_identity_authority_survives_restarts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "oin-k2-process.db"
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_spki = b64url_encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key = load_p256_public_key(public_key_spki)

    with _running_control_plane(database_path, tmp_path, 1) as client:
        registration = client.post(
            "/api/v1/accounts",
            json={
                "username": "pdd-k2-process-owner",
                "email": "pdd-k2-process-owner@example.test",
                "password": "correct horse battery 2026!",
            },
        )
        assert registration.status_code == 201, registration.text
        actor, token = _login(client)

        _controller_post(
            client,
            token,
            "/api/v1/operations/workspace-bootstrap",
            {
                "idempotency_key": "idem.k2.process.bootstrap.001",
                "tenant_id": TENANT_ID,
                "tenant_owner_membership_id": "membership_k2_process_tenant",
                "merchant_id": MERCHANT_ID,
                "merchant_display_name": "K2 process merchant",
                "merchant_owner_membership_id": MEMBERSHIP_ID,
            },
        )
        _controller_post(
            client,
            token,
            "/api/v1/operations/shops",
            {
                "idempotency_key": "idem.k2.process.shop.001",
                "tenant_id": TENANT_ID,
                "merchant_id": MERCHANT_ID,
                "shop_id": SHOP_ID,
                "platform": "pinduoduo",
                "display_name": "K2 process PDD shop",
                "provider_identity": {
                    "provider_platform_shop_id": None,
                    "provider_platform_shop_ref": None,
                    "identity_quality": "unknown",
                },
            },
        )
        _controller_post(
            client,
            token,
            "/api/v1/operations/principals",
            {
                "idempotency_key": "idem.k2.process.principal.001",
                "tenant_id": TENANT_ID,
                "merchant_id": MERCHANT_ID,
                "principal_id": PRINCIPAL_ID,
                "platform": "pinduoduo",
                "display_name": "K2 process PDD principal",
                "role_note": None,
                "shop_ids": [SHOP_ID],
                "provider_identity": {
                    "provider_platform_principal_id": None,
                    "provider_platform_principal_ref": None,
                    "identity_quality": "unknown",
                },
            },
        )
        enrollment = _controller_post(
            client,
            token,
            "/api/v1/operations/enrollment-authorizations",
            {
                "idempotency_key": "idem.k2.process.enrollment.001",
                "authorization_id": "enrollment_k2_process_001",
                "tenant_id": TENANT_ID,
                "merchant_id": MERCHANT_ID,
                "connector_installation_id": INSTALLATION_ID,
                "public_key_spki": public_key_spki,
            },
        )["enrollment"]
        challenge_request = {
            "contract": {"name": "oin.runtime", "version": 2},
            "envelope": {
                "command_id": "command_k2_process_challenge_001",
                "idempotency_key": "idem.k2.process.challenge.001",
                "command_type": "runtime.registration.challenge.request",
                "issued_at": _now(),
                "producer": {
                    "actor_kind": "connector",
                    "actor_id": INSTALLATION_ID,
                },
            },
            "wire_direction": "connector_to_control_plane",
            "connector_installation_id": INSTALLATION_ID,
            "agent_version": "0.1.0",
            "enrollment": _enrollment_reference(enrollment),
        }
        challenge = _runtime_post(
            client,
            "/api/v2/runtime/registration-challenges",
            challenge_request,
        ).json()
        connector_request = _registration_request(
            enrollment,
            challenge,
            private_key,
            command_id="command_k2_process_connector_001",
            idempotency_key="idem.k2.process.connector.001",
        )
        connector_id = _runtime_post(
            client,
            "/api/v2/runtime/connectors",
            connector_request,
        ).json()["connector_id"]
        _controller_post(
            client,
            token,
            "/api/v1/operations/profiles",
            {
                "idempotency_key": "idem.k2.process.profile.001",
                "tenant_id": TENANT_ID,
                "connector_id": connector_id,
                "profile_id": PROFILE_ID,
            },
        )
        _controller_post(
            client,
            token,
            "/api/v1/operations/bindings",
            {
                "idempotency_key": "idem.k2.process.binding.001",
                "tenant_id": TENANT_ID,
                "merchant_id": MERCHANT_ID,
                "shop_id": SHOP_ID,
                "platform": "pinduoduo",
                "principal_id": PRINCIPAL_ID,
                "connector_id": connector_id,
                "profile_id": PROFILE_ID,
                "profile_generation": 1,
                "binding_id": BINDING_ID,
                "runtime_kind": "windows_web",
                "adapter_id": "pinduoduo.mms.web",
                "adapter_version": "0.1.0",
                "capabilities": ["message.observe"],
            },
        )
        activation_command = {
            "tenant_id": TENANT_ID,
            "connector_id": connector_id,
            "profile_id": PROFILE_ID,
            "binding_id": BINDING_ID,
        }
        activation = _controller_post(
            client,
            token,
            "/api/v1/operations/route-activations",
            activation_command,
        )["activation"]
        consumed = _runtime_post(
            client,
            "/api/v2/runtime/route-activations/consume",
            _route_activation_request(
                activation["activation_code"],
                INSTALLATION_ID,
                private_key,
            ),
        ).json()
        assert consumed["expected_platform_identity"]["identity_state"] == (
            "PENDING"
        )

        observation = _identity_observation(
            connector_id=connector_id,
            expected_identity=consumed["expected_platform_identity"],
            suffix="001",
        )
        admitted = _runtime_post(
            client,
            "/api/v2/runtime/identity-observations",
            _ingress_frame(observation, private_key, p256_key_id(public_key)),
        )
        assert admitted.json()["observation"]["status"] == "PENDING"
        assert admitted.json()["disposition"] == "applied"

        stale_activation = _controller_post(
            client,
            token,
            "/api/v1/operations/route-activations",
            activation_command,
        )["activation"]
        context = client.get(
            "/api/v1/operations/authorized-context",
            headers=_bearer(token),
        )
        assert context.status_code == 200, context.text
        membership = next(
            item
            for item in context.json()["merchant_memberships"]
            if item["membership_id"] == MEMBERSHIP_ID
        )
        membership_revision = membership["meta"]["revision"]

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        pending = connection.execute(
            """
            SELECT observation_state FROM identity_evidence
            WHERE evidence_id = 'evidence_k2_process_001'
            """
        ).fetchone()
        states = connection.execute(
            """
            SELECT
              (SELECT identity_state FROM platform_shops WHERE id = ?) AS shop,
              (SELECT identity_state FROM platform_principals WHERE id = ?) AS principal,
              (SELECT identity_state FROM platform_account_bindings WHERE id = ?) AS binding
            """,
            (SHOP_ID, PRINCIPAL_ID, BINDING_ID),
        ).fetchone()
    assert pending["observation_state"] == "PENDING"
    assert dict(states) == {
        "shop": "PENDING",
        "principal": "PENDING",
        "binding": "PENDING",
    }

    command = _confirmation_command(actor, membership_revision, observation)
    with _running_control_plane(database_path, tmp_path, 2) as client:
        restarted_actor, token = _login(client)
        assert restarted_actor["id"] == actor["id"]
        pending_response = client.get(
            "/api/v1/operations/identity-evidence/pending",
            headers=_bearer(token),
        )
        assert pending_response.status_code == 200, pending_response.text
        assert [
            item["evidence_id"] for item in pending_response.json()["items"]
        ] == [observation["evidence"]["evidence_id"]]
        confirmation = client.post(
            "/api/v1/operations/identity-confirmations",
            headers=_bearer(token),
            json=command,
        )
        assert confirmation.status_code == 201, confirmation.text
        assert confirmation.json()["confirmation"]["result"] == "CONFIRMED"
        authority = confirmation.json()["route_authority"]
        assert authority["identity_state"] == "VERIFIED"
        assert authority["binding_lifecycle_state"] == "ACTIVE"
        assert authority["verified_identity_revision"] == 1
        assert authority["fresh_route_activation_required"] is True
        assert "activation_code" not in confirmation.text

        replay = client.post(
            "/api/v1/operations/identity-confirmations",
            headers=_bearer(token),
            json=command,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["confirmation"]["result"] == "IDEMPOTENT_REPLAY"

        stale_consume = _runtime_post(
            client,
            "/api/v2/runtime/route-activations/consume",
            _route_activation_request(
                stale_activation["activation_code"],
                INSTALLATION_ID,
                private_key,
            ),
            expected_status=401,
        )
        assert stale_consume.json()["error"]["code"] == (
            "authentication_failed"
        )

        stale_observation = copy.deepcopy(observation)
        stale_observation["envelope"]["event_id"] = "event_k2_process_stale"
        stale_observation["envelope"]["idempotency_key"] = (
            "idem.k2.process.observation.stale"
        )
        stale_observation["evidence"]["evidence_id"] = (
            "evidence_k2_process_stale"
        )
        stale_observation["evidence"]["evidence_sha256"] = (
            pdd_identity_evidence_sha256(stale_observation)
        )
        stale_ingress = _runtime_post(
            client,
            "/api/v2/runtime/identity-observations",
            _ingress_frame(
                stale_observation,
                private_key,
                p256_key_id(public_key),
            ),
            expected_status=409,
        )
        assert stale_ingress.json()["error"]["detail_code"] == (
            "identity_observation_requires_pending_route"
        )

        fresh_activation = _controller_post(
            client,
            token,
            "/api/v1/operations/route-activations",
            activation_command,
        )["activation"]
        fresh_route = _runtime_post(
            client,
            "/api/v2/runtime/route-activations/consume",
            _route_activation_request(
                fresh_activation["activation_code"],
                INSTALLATION_ID,
                private_key,
            ),
        ).json()["expected_platform_identity"]
        assert fresh_route["identity_state"] == "VERIFIED"
        assert fresh_route["shop_revision"] == authority["shop_revision"]
        assert fresh_route["principal_revision"] == authority[
            "principal_revision"
        ]
        assert fresh_route["binding_revision"] == authority[
            "binding_revision"
        ]

    with _running_control_plane(database_path, tmp_path, 3) as client:
        _, token = _login(client)
        assert client.get(
            "/api/v1/operations/identity-evidence/pending",
            headers=_bearer(token),
        ).json()["items"] == []
        persisted_activation = _controller_post(
            client,
            token,
            "/api/v1/operations/route-activations",
            activation_command,
        )["activation"]
        persisted_route = _runtime_post(
            client,
            "/api/v2/runtime/route-activations/consume",
            _route_activation_request(
                persisted_activation["activation_code"],
                INSTALLATION_ID,
                private_key,
            ),
        ).json()["expected_platform_identity"]
        assert persisted_route == fresh_route

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        final = connection.execute(
            """
            SELECT
              (SELECT identity_state FROM platform_shops WHERE id = ?) AS shop_state,
              (SELECT provider_shop_id FROM platform_shops WHERE id = ?) AS shop_provider,
              (SELECT identity_state FROM platform_principals WHERE id = ?) AS principal_state,
              (SELECT provider_principal_id FROM platform_principals WHERE id = ?) AS principal_provider,
              (SELECT state FROM platform_account_bindings WHERE id = ?) AS binding_lifecycle,
              (SELECT identity_state FROM platform_account_bindings WHERE id = ?) AS binding_state,
              (SELECT verified_identity_revision FROM platform_account_bindings WHERE id = ?) AS verified_revision,
              (SELECT COUNT(*) FROM identity_confirmations) AS confirmation_count
            """,
            (
                SHOP_ID,
                SHOP_ID,
                PRINCIPAL_ID,
                PRINCIPAL_ID,
                BINDING_ID,
                BINDING_ID,
                BINDING_ID,
            ),
        ).fetchone()
        audit_details = "\n".join(
            row["detail_json"]
            for row in connection.execute(
                """
                SELECT detail_json FROM audit_logs
                WHERE action IN (
                    'pdd_identity_observation_persisted',
                    'pdd_identity_confirmed'
                )
                """
            ).fetchall()
        )
    assert dict(final) == {
        "shop_state": "VERIFIED",
        "shop_provider": "mall-k2-process-1001",
        "principal_state": "VERIFIED",
        "principal_provider": "user-k2-process-2001",
        "binding_lifecycle": "ACTIVE",
        "binding_state": "VERIFIED",
        "verified_revision": 1,
        "confirmation_count": 1,
    }
    assert "mall-k2-process-1001" not in audit_details
    assert "user-k2-process-2001" not in audit_details
    assert "K2 process evidence mall" not in audit_details
