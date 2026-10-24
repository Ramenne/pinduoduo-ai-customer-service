from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oin_control_plane.app import create_app
from oin_control_plane.config import Settings
from oin_control_plane.security import (
    b64url_encode,
    enrollment_message,
    websocket_auth_message,
    websocket_transport_auth_message,
)


@pytest.fixture()
def app(tmp_path: Path):
    apk = tmp_path / "oin-agent-test.apk"
    apk.write_bytes(b"test-apk-not-installable")
    settings = Settings(
        database_path=tmp_path / "oin-test.db",
        public_base_url="https://testserver",
        apk_path=apk,
        apk_version="0.1.0-test",
        environment="test",
        password_iterations=100_000,
        websocket_auth_timeout_seconds=2,
        heartbeat_timeout_seconds=3,
        dispatch_freshness_seconds=2,
        task_ack_deadline_seconds=1,
        websocket_send_timeout_seconds=2,
        websocket_close_timeout_seconds=1,
        cleanup_interval_seconds=0,
    )
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app, base_url="https://testserver") as value:
        yield value


def account(client: TestClient, username: str, password: str = "correct horse battery") -> dict[str, Any]:
    response = client.post(
        "/api/v1/accounts",
        json={"username": username, "email": f"{username}@example.test", "password": password},
    )
    assert response.status_code == 201, response.text
    login = client.post(
        "/api/v1/mobile/sessions",
        json={"identifier": username, "password": password},
    )
    assert login.status_code == 200, login.text
    return {"account": response.json()["account"], "token": login.json()["access_token"]}


def controller_headers(app, account_record: dict[str, Any]) -> dict[str, str]:
    token, csrf, _ = app.state.store.create_web_session(account_record["account"]["id"])
    return {"Cookie": f"oin_session={token}", "X-CSRF-Token": csrf}


def device_payload(name: str = "Test Android") -> dict[str, Any]:
    return {
        "display_name": name,
        "installation_id": "install-9caa62a7",
        "manufacturer": "TestMaker",
        "brand": "TestBrand",
        "model": "Model 9",
        "android_version": "9",
        "sdk": 28,
        "app_version": "0.3.1-v01-test",
        "app_version_code": 5,
        "kernel_version": "0.2.1-dev",
        "timezone": "Asia/Shanghai",
        "network_type": "wifi",
        "capabilities": {
            "accessibility_enabled": True,
            "accessibility_connected": True,
            "notification_enabled": True,
            "notification_connected": True,
            "battery_optimization_ignored": False,
            "background_start_user_configured": False,
            "xianyu_installed": True,
            "xianyu_version": "test",
            "xianyu_rules_version": "0.2.1-dev",
            "xianyu_rules_verified": False,
            "screenshot_supported": False,
            "root_signal": False,
            "local_executor_version": "0.2.1-dev",
            "cloud_task_types": ["action", "health", "observe", "xianyu_snapshot"],
        },
    }


def public_key_text(private_key: ec.EllipticCurvePrivateKey) -> str:
    return b64url_encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def sign(private_key: ec.EllipticCurvePrivateKey, message: str) -> str:
    return b64url_encode(private_key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256())))


def enroll(
    client: TestClient,
    token: str,
    *,
    private_key: ec.EllipticCurvePrivateKey | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    private_key = private_key or ec.generate_private_key(ec.SECP256R1())
    payload = payload or device_payload()
    headers = {"Authorization": f"Bearer {token}"}
    challenge_response = client.post(
        "/api/v1/device-enrollment-challenges",
        json={"public_key": public_key_text(private_key)},
        headers=headers,
    )
    assert challenge_response.status_code == 201, challenge_response.text
    challenge = challenge_response.json()
    signature = sign(
        private_key,
        enrollment_message(challenge["challenge_id"], challenge["nonce"], payload),
    )
    response = client.post(
        "/api/v1/devices",
        json={
            "challenge_id": challenge["challenge_id"],
            "payload": payload,
            "signature": signature,
        },
        headers=headers,
    )
    assert response.status_code in {200, 201}, response.text
    return {
        "device": response.json()["device"],
        "private_key": private_key,
        "payload": payload,
        "registration": response.json()["registration"],
    }


def websocket_auth(
    socket,
    device_id: str,
    private_key: ec.EllipticCurvePrivateKey,
    payload: dict[str, Any],
) -> dict[str, Any]:
    challenge = socket.receive_json()
    assert challenge["type"] == "auth_challenge"
    message = websocket_auth_message(
        device_id,
        challenge["challenge_id"],
        challenge["nonce"],
        challenge["issued_at"],
        payload,
    )
    socket.send_json(
        {
            "type": "auth",
            "protocol_version": "1",
            "challenge_id": challenge["challenge_id"],
            "device_id": device_id,
            "signature": sign(private_key, message),
            "payload": payload,
        }
    )
    response = socket.receive_json()
    assert response["type"] == "auth_ok"
    return response


def websocket_transport_auth(
    socket,
    device_id: str,
    private_key: ec.EllipticCurvePrivateKey,
    *,
    app_version_code: int = 26,
    task_store_protocol: int = 2,
) -> dict[str, Any]:
    challenge = socket.receive_json()
    assert challenge["type"] == "auth_challenge"
    transport_auth = {
        "contract": "code26_transport_v1",
        "app_version_code": app_version_code,
        "task_store_protocol": task_store_protocol,
    }
    message = websocket_transport_auth_message(
        device_id,
        challenge["challenge_id"],
        challenge["nonce"],
        challenge["issued_at"],
        transport_auth,
    )
    socket.send_json(
        {
            "type": "auth",
            "protocol_version": "1",
            "challenge_id": challenge["challenge_id"],
            "device_id": device_id,
            "signature": sign(private_key, message),
            "transport_auth": transport_auth,
        }
    )
    response = socket.receive_json()
    assert response["type"] == "auth_ok"
    return response


def csrf_from_html(value: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', value)
    assert match
    return match.group(1)
