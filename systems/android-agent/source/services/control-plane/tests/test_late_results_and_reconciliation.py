from __future__ import annotations

from copy import deepcopy
import hashlib

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
import pytest

from conftest import (
    account,
    controller_headers,
    device_payload,
    enroll,
    public_key_text,
    sign,
    websocket_auth,
)
from oin_control_plane.security import (
    canonical_json,
    credential_repair_message,
    now_seconds,
)
from oin_control_plane.store import AuthenticationError, TaskCompletionKind


def _task_store_v2_payload(name: str, installation_id: str) -> dict:
    payload = device_payload(name)
    payload["installation_id"] = installation_id
    capabilities = payload["capabilities"]
    capabilities.pop("background_start_user_configured")
    capabilities.update(
        {
            "background_start_status": "unknown",
            "task_store_protocol": 2,
            "task_channel_ready": True,
            "task_store_state": "ready",
        }
    )
    return payload


def _result_digest(status: str, *, result=None, error=None) -> tuple[str, int]:
    envelope = {"status": status}
    if result is not None:
        envelope["result"] = result
    if error is not None:
        envelope["error"] = error
    encoded = canonical_json(envelope)
    encoded_bytes = encoded.encode("utf-8")
    return hashlib.sha256(encoded_bytes).hexdigest(), len(encoded_bytes)


def test_expired_real_result_is_persisted_before_ack_and_conflict_is_not_acked(
    client: TestClient, app
) -> None:
    owner = account(client, "late-result-owner")
    payload = _task_store_v2_payload("Late result phone", "install-late-result")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(socket, device_id, enrolled["private_key"], payload)
        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json={
                "task_type": "health",
                "parameters": {},
                "ttl_seconds": 60,
                "idempotency_key": "late-result-health-001",
            },
            headers=headers,
        )
        task_id = created.json()["task"]["id"]
        assert socket.receive_json()["task_id"] == task_id
        assert app.state.store.mark_task_running(device_id, task_id)
        with app.state.database.connect() as connection:
            connection.execute(
                "UPDATE device_tasks SET expires_at = ? WHERE id = ?",
                (now_seconds() - 1, task_id),
            )

        exact = {
            "type": "task_result",
            "protocol_version": "1",
            "task_id": task_id,
            "status": "succeeded",
            "result": {"source": "real-device", "value": 7},
        }
        socket.send_json(exact)
        assert socket.receive_json() == {
            "type": "task_result_ack",
            "protocol_version": "1",
            "task_id": task_id,
        }

        persisted = client.get(
            f"/api/v1/devices/{device_id}/tasks/{task_id}", headers=headers
        ).json()["task"]
        assert persisted["status"] == "expired"
        assert persisted["effective_status"] == "expired_with_late_result"
        assert persisted["result_disposition"] == "expired_with_late_result"
        assert persisted["reported_result"] == {
            "status": "succeeded",
            "result": {"source": "real-device", "value": 7},
        }
        assert persisted["result_received_at"] is not None

        socket.send_json(exact)
        assert socket.receive_json()["type"] == "task_result_ack"

        conflicting = deepcopy(exact)
        conflicting["result"] = {"source": "real-device", "value": 8}
        socket.send_json(conflicting)
        assert socket.receive_json() == {
            "type": "error",
            "protocol_version": "1",
            "code": "task_result_conflict",
            "task_id": task_id,
        }

    unchanged = client.get(
        f"/api/v1/devices/{device_id}/tasks/{task_id}", headers=headers
    ).json()["task"]
    assert unchanged["reported_result"]["result"]["value"] == 7


def test_disconnect_resend_exact_result_is_acknowledged_without_data_loss(
    client: TestClient, app
) -> None:
    owner = account(client, "resend-owner")
    payload = _task_store_v2_payload("Resend phone", "install-resend")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    task, _ = app.state.store.create_task(
        owner["account"]["id"],
        device_id,
        "health",
        "disconnect-resend-001",
        {},
        60,
    )
    app.state.store.mark_task_running(device_id, task["id"])
    with app.state.database.connect() as connection:
        connection.execute(
            "UPDATE device_tasks SET expires_at = ? WHERE id = ?",
            (now_seconds() - 1, task["id"]),
        )
    first = app.state.store.complete_task(
        device_id,
        task["id"],
        "succeeded",
        {"durable": True},
        None,
        None,
    )
    assert first.kind is TaskCompletionKind.STORED

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(socket, device_id, enrolled["private_key"], payload)
        socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": task["id"],
                "status": "succeeded",
                "result": {"durable": True},
            }
        )
        assert socket.receive_json()["type"] == "task_result_ack"

    repeated = app.state.store.complete_task(
        device_id,
        task["id"],
        "succeeded",
        {"durable": True},
        None,
        None,
    )
    assert repeated.kind is TaskCompletionKind.EXACT_DUPLICATE


def test_same_owner_credential_repair_rotates_key_without_changing_device_id(
    client: TestClient, app
) -> None:
    owner = account(client, "repair-owner")
    stranger = account(client, "repair-stranger")
    payload = _task_store_v2_payload("Repair phone", "install-repair")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    replacement = ec.generate_private_key(ec.SECP256R1())
    mobile_headers = {"Authorization": f"Bearer {owner['token']}"}
    controller = controller_headers(app, owner)
    old_credential = app.state.store.get_device_credential(device_id)
    assert old_credential is not None

    cross_owner = client.post(
        f"/api/v1/devices/{device_id}/credential-repair-requests",
        json={"public_key": public_key_text(replacement)},
        headers={"Authorization": f"Bearer {stranger['token']}"},
    )
    assert cross_owner.status_code == 404

    request_response = client.post(
        f"/api/v1/devices/{device_id}/credential-repair-requests",
        json={"public_key": public_key_text(replacement)},
        headers=mobile_headers,
    )
    assert request_response.status_code == 201
    repair_request = request_response.json()["repair_request"]
    assert repair_request["state"] == "pending_controller_approval"
    assert "nonce" not in repair_request

    mobile_cannot_approve = client.post(
        f"/api/v1/devices/{device_id}/credential-repair-requests/{repair_request['id']}/approve",
        headers=mobile_headers,
    )
    assert mobile_cannot_approve.status_code == 401

    approved = client.post(
        f"/api/v1/devices/{device_id}/credential-repair-requests/{repair_request['id']}/approve",
        headers=controller,
    )
    assert approved.status_code == 201
    challenge = client.get(
        f"/api/v1/devices/{device_id}/credential-repair-requests/{repair_request['id']}",
        headers=mobile_headers,
    ).json()["repair_request"]
    assert challenge["state"] == "approved"
    message = credential_repair_message(
        device_id,
        challenge["challenge_id"],
        challenge["nonce"],
        payload,
    )
    repaired = client.post(
        f"/api/v1/devices/{device_id}/credential-repair",
        json={
            "challenge_id": challenge["challenge_id"],
            "payload": payload,
            "signature": sign(replacement, message),
        },
        headers=mobile_headers,
    )
    assert repaired.status_code == 200
    assert repaired.json()["device"]["id"] == device_id
    assert repaired.json()["repair"] == "rotated"

    repeated = client.post(
        f"/api/v1/devices/{device_id}/credential-repair",
        json={
            "challenge_id": challenge["challenge_id"],
            "payload": payload,
            "signature": sign(replacement, message),
        },
        headers=mobile_headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["repair"] == "existing"
    completed_request = client.post(
        f"/api/v1/devices/{device_id}/credential-repair-requests",
        json={"public_key": public_key_text(replacement)},
        headers=mobile_headers,
    )
    assert completed_request.status_code == 200
    assert completed_request.json()["repair_request"]["id"] == repair_request["id"]
    assert completed_request.json()["repair_request"]["state"] == "completed"
    credential = app.state.store.get_device_credential(device_id)
    assert credential is not None
    assert credential["public_key_sha256"] != old_credential["public_key_sha256"]

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(socket, device_id, replacement, payload)

    expired_key = ec.generate_private_key(ec.SECP256R1())
    expired_request = client.post(
        f"/api/v1/devices/{device_id}/credential-repair-requests",
        json={"public_key": public_key_text(expired_key)},
        headers=mobile_headers,
    ).json()["repair_request"]
    expired_approval = client.post(
        f"/api/v1/devices/{device_id}/credential-repair-requests/{expired_request['id']}/approve",
        headers=controller,
    ).json()["repair_request"]
    with app.state.database.connect() as connection:
        connection.execute(
            "UPDATE device_credential_repair_challenges SET expires_at = ? WHERE id = ?",
            (now_seconds() - 1, expired_request["id"]),
        )
    polled_expired = client.get(
        f"/api/v1/devices/{device_id}/credential-repair-requests/{expired_request['id']}",
        headers=mobile_headers,
    ).json()["repair_request"]
    assert polled_expired["state"] == "expired"
    expired_message = credential_repair_message(
        device_id,
        expired_request["id"],
        expired_approval["nonce"],
        payload,
    )
    rejected_expired = client.post(
        f"/api/v1/devices/{device_id}/credential-repair",
        json={
            "challenge_id": expired_request["id"],
            "payload": payload,
            "signature": sign(expired_key, expired_message),
        },
        headers=mobile_headers,
    )
    assert rejected_expired.status_code == 401


def test_outbox_reconciliation_requires_exact_owner_revoked_old_and_bound_new(
    client: TestClient, app
) -> None:
    owner = account(client, "reconcile-owner")
    stranger = account(client, "reconcile-stranger")
    old_payload = _task_store_v2_payload("Old phone", "install-reconcile-agent")
    new_payload = _task_store_v2_payload("New phone", "install-reconcile-agent")
    stranger_payload = _task_store_v2_payload("Other phone", "install-other")
    old = enroll(client, owner["token"], payload=old_payload)
    new = enroll(client, owner["token"], payload=new_payload)
    alternate_new = enroll(
        client,
        owner["token"],
        payload=_task_store_v2_payload("Alternate new phone", "install-new-alt"),
    )
    other = enroll(client, stranger["token"], payload=stranger_payload)
    headers = controller_headers(app, owner)

    not_revoked = client.post(
        f"/api/v1/devices/{new['device']['id']}/outbox-reconciliations",
        json={
            "old_device_id": old["device"]["id"],
            "idempotency_key": "reconcile-not-revoked-001",
        },
        headers=headers,
    )
    assert not_revoked.status_code == 409
    assert not_revoked.json()["error"]["code"] == "old_device_not_revoked"

    assert app.state.store.revoke_device(
        owner["account"]["id"], old["device"]["id"]
    )
    wrong_installation = client.post(
        f"/api/v1/devices/{alternate_new['device']['id']}/outbox-reconciliations",
        json={
            "old_device_id": old["device"]["id"],
            "idempotency_key": "reconcile-wrong-installation-001",
        },
        headers=headers,
    )
    assert wrong_installation.status_code == 409
    assert (
        wrong_installation.json()["error"]["code"]
        == "reconciliation_installation_mismatch"
    )
    cross_account = client.post(
        f"/api/v1/devices/{other['device']['id']}/outbox-reconciliations",
        json={
            "old_device_id": old["device"]["id"],
            "idempotency_key": "reconcile-cross-owner-001",
        },
        headers=headers,
    )
    assert cross_account.status_code == 404

    created = client.post(
        f"/api/v1/devices/{new['device']['id']}/outbox-reconciliations",
        json={
            "old_device_id": old["device"]["id"],
            "idempotency_key": "reconcile-exact-001",
        },
        headers=headers,
    )
    assert created.status_code == 201
    repeated = client.post(
        f"/api/v1/devices/{new['device']['id']}/outbox-reconciliations",
        json={
            "old_device_id": old["device"]["id"],
            "idempotency_key": "reconcile-exact-001",
        },
        headers=headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["authorization"]["id"] == created.json()["authorization"]["id"]
    repeated_with_new_request_key = client.post(
        f"/api/v1/devices/{new['device']['id']}/outbox-reconciliations",
        json={
            "old_device_id": old["device"]["id"],
            "idempotency_key": "reconcile-exact-controller-retry-002",
        },
        headers=headers,
    )
    assert repeated_with_new_request_key.status_code == 200
    assert (
        repeated_with_new_request_key.json()["authorization"]["id"]
        == created.json()["authorization"]["id"]
    )

    idempotency_scope_conflict = client.post(
        f"/api/v1/devices/{alternate_new['device']['id']}/outbox-reconciliations",
        json={
            "old_device_id": old["device"]["id"],
            "idempotency_key": "reconcile-exact-001",
        },
        headers=headers,
    )
    assert idempotency_scope_conflict.status_code == 409
    authorization = app.state.store.pending_outbox_reconciliation(new["device"]["id"])
    assert authorization is not None
    with pytest.raises(AuthenticationError, match="reconciliation_scope_mismatch"):
        app.state.store.begin_outbox_reconciliation(
            new["device"]["id"],
            authorization["id"],
            authorization["challenge"],
            other["device"]["id"],
            new["device"]["id"],
            [],
        )
    with pytest.raises(AuthenticationError, match="reconciliation_challenge_mismatch"):
        app.state.store.begin_outbox_reconciliation(
            new["device"]["id"],
            authorization["id"],
            "wrong-one-time-challenge",
            old["device"]["id"],
            new["device"]["id"],
            [],
        )
    with app.state.database.connect() as connection:
        connection.execute(
            "UPDATE outbox_reconciliation_authorizations SET expires_at = ? WHERE id = ?",
            (now_seconds() - 1, authorization["id"]),
        )
    with pytest.raises(AuthenticationError, match="reconciliation_authorization_expired"):
        app.state.store.begin_outbox_reconciliation(
            new["device"]["id"],
            authorization["id"],
            authorization["challenge"],
            old["device"]["id"],
            new["device"]["id"],
            [],
        )


def test_revoked_foreign_pending_and_running_rows_reconcile_without_blind_purge(
    client: TestClient, app
) -> None:
    owner = account(client, "foreign-outbox-owner")
    old_payload = _task_store_v2_payload("Old scope", "install-foreign-agent")
    new_payload = _task_store_v2_payload("New scope", "install-foreign-agent")
    old = enroll(client, owner["token"], payload=old_payload)
    new = enroll(client, owner["token"], payload=new_payload)
    old_id = old["device"]["id"]
    new_id = new["device"]["id"]
    account_id = owner["account"]["id"]
    pending_task, _ = app.state.store.create_task(
        account_id, old_id, "health", "foreign-pending-001", {}, 60
    )
    running_task, _ = app.state.store.create_task(
        account_id, old_id, "health", "foreign-running-001", {}, 60
    )
    app.state.store.mark_task_running(old_id, pending_task["id"])
    app.state.store.mark_task_running(old_id, running_task["id"])
    assert app.state.store.revoke_device(account_id, old_id)

    headers = controller_headers(app, owner)
    authorized = client.post(
        f"/api/v1/devices/{new_id}/outbox-reconciliations",
        json={
            "old_device_id": old_id,
            "idempotency_key": "foreign-outbox-reconcile-001",
        },
        headers=headers,
    )
    assert authorized.status_code == 201
    authorization_id = authorized.json()["authorization"]["id"]
    result = {"late": True, "source": "durable-old-scope"}
    result_sha256, result_bytes = _result_digest("succeeded", result=result)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        auth_ok = websocket_auth(socket, new_id, new["private_key"], new_payload)
        directive = auth_ok["outbox_reconciliation"]
        assert directive["authorization_id"] == authorization_id
        assert directive["old_scope"] == old_id
        assert directive["new_scope"] == new_id

        begin = {
            "type": "outbox_reconciliation_begin",
            "protocol_version": "1",
            "authorization_id": authorization_id,
            "challenge": directive["challenge"],
            "old_scope": old_id,
            "new_scope": new_id,
            "items": [
                {
                    "task_id": pending_task["id"],
                    "local_state": "PENDING",
                    "result_sha256": result_sha256,
                    "result_bytes": result_bytes,
                },
                {
                    "task_id": running_task["id"],
                    "local_state": "RUNNING",
                    "result_sha256": "",
                    "result_bytes": 0,
                },
            ],
        }
        socket.send_json(begin)
        assert socket.receive_json()["type"] == "outbox_reconciliation_begin_ack"

        pending_item = {
            "type": "outbox_reconciliation_item",
            "protocol_version": "1",
            "authorization_id": authorization_id,
            "challenge": directive["challenge"],
            "old_scope": old_id,
            "new_scope": new_id,
            "task_id": pending_task["id"],
            "local_state": "PENDING",
            "status": "succeeded",
            "result": result,
        }
        socket.send_json(pending_item)
        pending_ack = socket.receive_json()
        assert pending_ack["type"] == "outbox_reconciliation_item_ack"
        assert pending_ack["disposition"] == "admin_revoked_with_late_result"
        assert pending_ack["result_sha256"] == result_sha256
        assert pending_ack["reconciliation_complete"] is False

        socket.send_json(pending_item)
        assert socket.receive_json()["type"] == "outbox_reconciliation_item_ack"

        conflict = deepcopy(pending_item)
        conflict["result"] = {"late": False}
        socket.send_json(conflict)
        assert socket.receive_json()["code"] == "reconciliation_result_conflict"

        socket.send_json(
            {
                "type": "outbox_reconciliation_item",
                "protocol_version": "1",
                "authorization_id": authorization_id,
                "challenge": directive["challenge"],
                "old_scope": old_id,
                "new_scope": new_id,
                "task_id": running_task["id"],
                "local_state": "RUNNING",
            }
        )
        running_ack = socket.receive_json()
        assert running_ack["disposition"] == "admin_revoked_interrupted"
        assert running_ack["reconciliation_complete"] is True

        socket.send_json(
            {
                "type": "outbox_reconciliation_complete",
                "protocol_version": "1",
                "authorization_id": authorization_id,
                "challenge": directive["challenge"],
                "old_scope": old_id,
                "new_scope": new_id,
            }
        )
        assert socket.receive_json()["type"] == "outbox_reconciliation_complete_ack"

    persisted_pending = app.state.store.get_task(
        account_id, old_id, pending_task["id"]
    )
    assert persisted_pending is not None
    assert persisted_pending["status"] == "rejected"
    assert persisted_pending["effective_status"] == "admin_revoked_with_late_result"
    assert persisted_pending["reported_result"]["result"] == result
    persisted_running = app.state.store.get_task(
        account_id, old_id, running_task["id"]
    )
    assert persisted_running is not None
    assert persisted_running["effective_status"] == "admin_revoked_interrupted"

    with client.websocket_connect("/api/v1/device-channel") as socket:
        auth_ok = websocket_auth(socket, new_id, new["private_key"], new_payload)
        assert "outbox_reconciliation" not in auth_ok
