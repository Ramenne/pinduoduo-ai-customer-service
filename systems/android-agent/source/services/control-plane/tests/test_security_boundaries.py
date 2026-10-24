from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import account, controller_headers, device_payload, enroll


def test_mobile_identity_cannot_act_as_controller_or_self_revoke(
    client: TestClient, app
) -> None:
    owner = account(client, "identity-owner")
    first_payload = device_payload("Owner phone A")
    first_payload["installation_id"] = "install-owner-phone-a"
    second_payload = device_payload("Owner phone B")
    second_payload["installation_id"] = "install-owner-phone-b"
    first = enroll(client, owner["token"], payload=first_payload)
    second = enroll(client, owner["token"], payload=second_payload)
    first_id = first["device"]["id"]
    second_id = second["device"]["id"]
    mobile_headers = {"Authorization": f"Bearer {owner['token']}"}
    web_headers = controller_headers(app, owner)

    assert client.get("/api/v1/accounts/me", headers=mobile_headers).status_code == 200
    assert client.get("/api/v1/devices", headers=mobile_headers).status_code == 401
    assert client.get(f"/api/v1/devices/{first_id}", headers=mobile_headers).status_code == 401
    assert client.post(
        f"/api/v1/devices/{first_id}/revoke", headers=mobile_headers
    ).status_code == 401

    request = {
        "task_type": "health",
        "parameters": {},
        "ttl_seconds": 60,
        "idempotency_key": "controller-domain-001",
    }
    assert client.post(
        f"/api/v1/devices/{first_id}/tasks", json=request, headers=mobile_headers
    ).status_code == 401

    listed = client.get("/api/v1/devices", headers=web_headers)
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["devices"]} == {first_id, second_id}
    created = client.post(
        f"/api/v1/devices/{first_id}/tasks", json=request, headers=web_headers
    )
    assert created.status_code == 201
    task_id = created.json()["task"]["id"]
    assert client.get(
        f"/api/v1/devices/{first_id}/tasks/{task_id}", headers=mobile_headers
    ).status_code == 401
    assert client.get(
        f"/api/v1/devices/{first_id}/tasks/{task_id}", headers=web_headers
    ).status_code == 200

    challenge_response = client.post(
        "/api/v1/device-revocation-challenges",
        json={"device_id": second_id},
        headers=mobile_headers,
    )
    assert challenge_response.status_code == 410
    assert challenge_response.json()["error"]["code"] == "mobile_self_revoke_disabled"

    self_revoke = client.post(
        f"/api/v1/devices/{second_id}/self-revoke",
        json={
            "challenge_id": "legacy-client-challenge",
            "signature": "legacy-client-signature",
        },
        headers=mobile_headers,
    )
    assert self_revoke.status_code == 410
    assert self_revoke.json()["error"]["code"] == "mobile_self_revoke_disabled"
    assert app.state.store.get_device_credential(second_id) is not None

    controller_revoke = client.post(
        f"/api/v1/devices/{second_id}/revoke", headers=web_headers
    )
    assert controller_revoke.status_code == 200
    assert app.state.store.get_device_credential(second_id) is None
    assert client.get(
        f"/api/v1/devices/{second_id}", headers=web_headers
    ).json()["device"]["state"] == "REVOKED"
