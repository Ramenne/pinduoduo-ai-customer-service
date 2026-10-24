from __future__ import annotations

import asyncio
import copy
import importlib
import json
import threading
import time
import uuid

from fastapi.testclient import TestClient

from conftest import (
    account,
    controller_headers,
    device_payload,
    enroll,
    sign,
    websocket_auth,
    websocket_transport_auth,
)
from oin_control_plane.hub import DeviceConnection
from oin_control_plane.security import (
    websocket_auth_message,
    websocket_transport_auth_message,
)


app_module = importlib.import_module("oin_control_plane.app")
store_module = importlib.import_module("oin_control_plane.store")


def test_code27_auth_binds_phone_epoch_to_exact_server_session(
    client: TestClient, app
) -> None:
    owner = account(client, "code27-session-binding-owner")
    payload = code26_payload(ready=True, state="ready")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]

    with client.websocket_connect("/api/v1/device-channel") as code26_socket:
        code26_auth = websocket_transport_auth(
            code26_socket,
            device_id,
            enrolled["private_key"],
            app_version_code=26,
        )
        assert "server_session_id" not in code26_auth

    with client.websocket_connect("/api/v1/device-channel") as code27_socket:
        code27_auth = websocket_transport_auth(
            code27_socket,
            device_id,
            enrolled["private_key"],
            app_version_code=27,
        )
        session_id = code27_auth["server_session_id"]
        assert str(uuid.UUID(session_id)) == session_id
        device = app.state.store.get_device(owner["account"]["id"], device_id)
        assert device is not None
        assert device["current_session_id"] == session_id

        code27_socket.send_json(
            {
                "type": "task_channel_state",
                "protocol_version": "1",
                "task_store_protocol": 2,
                "state": "ready",
                "ready": True,
                "failure_stage": "none",
            }
        )
        assert code27_socket.receive_json()["type"] == "task_channel_state_ack"
        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request("code27-session-timeline-001"),
            headers=controller_headers(app, owner),
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        legacy_task = code27_socket.receive_json()
        assert legacy_task["task_id"] == task_id
        assert set(legacy_task) == {
            "type",
            "protocol_version",
            "task_id",
            "task_type",
            "issued_at",
            "expires_at",
            "parameters",
        }
        code27_socket.send_json(
            {"type": "task_ack", "protocol_version": "1", "task_id": task_id}
        )
        code27_socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": task_id,
                "status": "succeeded",
                "result": {"ok": True},
            }
        )
        received = [code27_socket.receive_json()]
        if received[0]["type"] != "task_result_ack":
            assert received[0]["type"] == "task"
            received.append(code27_socket.receive_json())
        assert received[-1]["type"] == "task_result_ack"
        with app.state.database.connect() as connection:
            rows = connection.execute(
                "SELECT event_type, detail_json FROM task_events "
                "WHERE task_id = ? AND event_type IN ('delivery_attempt', 'running', 'succeeded') "
                "ORDER BY id",
                (task_id,),
            ).fetchall()
        assert [row["event_type"] for row in rows] == [
            "delivery_attempt",
            "running",
            "succeeded",
        ]
        assert all(
            json.loads(row["detail_json"])["server_session_id"] == session_id
            for row in rows
        )


def recovery_payload(*, ready: bool, state: str) -> dict:
    payload = copy.deepcopy(device_payload("Recovery Android"))
    capabilities = payload["capabilities"]
    capabilities.pop("background_start_user_configured")
    capabilities.update(
        {
            "background_start_status": "unknown",
            "notification_enabled": True,
            "task_store_protocol": 2,
            "task_channel_ready": ready,
            "task_store_state": state,
        }
    )
    return payload


def code26_payload(*, ready: bool, state: str) -> dict:
    payload = recovery_payload(ready=ready, state=state)
    payload["app_version"] = "0.9.3-v01-dev"
    payload["app_version_code"] = 26
    return payload


def task_request(idempotency_key: str) -> dict:
    return {
        "task_type": "health",
        "parameters": {},
        "ttl_seconds": 60,
        "idempotency_key": idempotency_key,
    }


def task_channel_state(*, ready: bool = True) -> dict:
    return {
        "type": "task_channel_state",
        "protocol_version": "1",
        "task_store_protocol": 2,
        "state": "ready" if ready else "unavailable",
        "ready": ready,
        "failure_stage": "none" if ready else "outbox_load",
    }


def test_code28_ack_timeout_reoffers_then_transport_staleness_closes_session(
    client: TestClient, app
) -> None:
    owner = account(client, "code28-ack-lease-owner")
    enrolled = enroll(
        client, owner["token"], payload=code26_payload(ready=True, state="ready")
    )
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as first_socket:
        first_auth = websocket_transport_auth(
            first_socket,
            device_id,
            enrolled["private_key"],
            app_version_code=28,
        )
        assert first_auth["task_recovery_safe_after"] == first_auth["server_time"]
        first_socket.send_json(task_channel_state())
        assert first_socket.receive_json()["type"] == "task_channel_state_ack"

        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request("code28-ack-lease-001"),
            headers=headers,
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        first_delivery = first_socket.receive_json()
        first_attempt = first_delivery["delivery_attempt_id"]
        assert first_delivery["task_id"] == task_id
        assert str(uuid.UUID(first_attempt)) == first_attempt

        # Test settings scale production 8s/90s to 1s/3s. The business ACK
        # deadline expires first while the authenticated transport is still fresh;
        # it must remain open and the next heartbeat must reoffer the same task.
        time.sleep(1.2)
        with app.state.database.connect() as connection:
            first_outcome = connection.execute(
                "SELECT outcome FROM task_delivery_attempts WHERE id = ?",
                (first_attempt,),
            ).fetchone()["outcome"]
        assert first_outcome == "ack_timeout"
        first_socket.send_json({"type": "heartbeat", "protocol_version": "1"})
        messages = [first_socket.receive_json(), first_socket.receive_json()]
        assert {message["type"] for message in messages} == {
            "heartbeat_ack",
            "task",
        }
        repeated = next(message for message in messages if message["type"] == "task")
        assert repeated["task_id"] == task_id
        assert repeated["delivery_attempt_id"] != first_attempt

        # Only transport liveness (3s without any authenticated client message),
        # not the 1s task ACK lease, is allowed to close this socket.
        close = first_socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4008
        assert close["reason"] == "heartbeat_or_auth_timeout"

    with client.websocket_connect("/api/v1/device-channel") as second_socket:
        second_auth = websocket_transport_auth(
            second_socket,
            device_id,
            enrolled["private_key"],
            app_version_code=28,
        )
        assert second_auth["task_recovery_safe_after"] == second_auth["server_time"]
        second_socket.send_json(task_channel_state())
        assert second_socket.receive_json()["type"] == "task_channel_state_ack"
        second_delivery = second_socket.receive_json()
        second_attempt = second_delivery["delivery_attempt_id"]
        assert second_delivery["task_id"] == task_id
        assert second_attempt != first_attempt

        second_socket.send_json(
            {
                "type": "task_ack",
                "protocol_version": "1",
                "task_id": task_id,
                "delivery_attempt_id": second_attempt,
            }
        )
        second_socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": task_id,
                "status": "succeeded",
                "result": {"ok": True},
            }
        )
        assert second_socket.receive_json() == {
            "type": "task_result_ack",
            "protocol_version": "1",
            "task_id": task_id,
        }

    stored = app.state.store.get_task(owner["account"]["id"], device_id, task_id)
    assert stored is not None and stored["status"] == "succeeded"
    with app.state.database.connect() as connection:
        attempts = connection.execute(
            "SELECT server_session_id, outcome FROM task_delivery_attempts "
            "WHERE task_id = ? ORDER BY prepared_at, id",
            (task_id,),
        ).fetchall()
    assert len(attempts) == 3
    assert {attempt["outcome"] for attempt in attempts} == {
        "ack_timeout",
        "acknowledged",
    }


def test_code28_sustained_heartbeat_redelivers_without_closing_healthy_transport(
    client: TestClient, app
) -> None:
    owner = account(client, "code28-healthy-unacked-owner")
    enrolled = enroll(
        client, owner["token"], payload=code26_payload(ready=True, state="ready")
    )
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_transport_auth(
            socket, device_id, enrolled["private_key"], app_version_code=28
        )
        socket.send_json(task_channel_state())
        assert socket.receive_json()["type"] == "task_channel_state_ack"
        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request("code28-healthy-unacked-001"),
            headers=headers,
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        first = socket.receive_json()
        assert first["task_id"] == task_id
        attempts = [first["delivery_attempt_id"]]

        for _ in range(3):
            time.sleep(0.35)
            socket.send_json({"type": "heartbeat", "protocol_version": "1"})
            messages = [socket.receive_json(), socket.receive_json()]
            assert {message["type"] for message in messages} == {
                "heartbeat_ack",
                "task",
            }
            repeated = next(
                message for message in messages if message["type"] == "task"
            )
            assert repeated["task_id"] == task_id
            attempts.append(repeated["delivery_attempt_id"])

        assert len(set(attempts)) == 4
        # The durable claim/SQLite path may finish after the first lease. A late
        # ACK for that known stale attempt must not kill the healthy current
        # transport or advance the task; the next heartbeat creates a fresh try.
        socket.send_json(
            {
                "type": "task_ack",
                "protocol_version": "1",
                "task_id": task_id,
                "delivery_attempt_id": attempts[0],
            }
        )
        socket.send_json({"type": "heartbeat", "protocol_version": "1"})
        messages = [socket.receive_json(), socket.receive_json()]
        assert {message["type"] for message in messages} == {
            "heartbeat_ack",
            "task",
        }
        latest = next(message for message in messages if message["type"] == "task")
        attempts.append(latest["delivery_attempt_id"])
        latest_attempt = attempts[-1]
        socket.send_json(
            {
                "type": "task_ack",
                "protocol_version": "1",
                "task_id": task_id,
                "delivery_attempt_id": latest_attempt,
            }
        )
        socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": task_id,
                "status": "succeeded",
                "result": {"healthy_transport": True},
            }
        )
        assert socket.receive_json()["type"] == "task_result_ack"
        socket.send_json({"type": "heartbeat", "protocol_version": "1"})
        assert socket.receive_json()["type"] == "heartbeat_ack"


def test_code28_requires_exact_attempt_ack_but_code27_contract_stays_legacy(
    client: TestClient, app
) -> None:
    owner = account(client, "code28-strict-attempt-owner")
    enrolled = enroll(
        client, owner["token"], payload=code26_payload(ready=True, state="ready")
    )
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_transport_auth(
            socket, device_id, enrolled["private_key"], app_version_code=28
        )
        socket.send_json(task_channel_state())
        assert socket.receive_json()["type"] == "task_channel_state_ack"
        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request("code28-strict-attempt-001"),
            headers=headers,
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        assert socket.receive_json()["task_id"] == task_id
        socket.send_json(
            {"type": "task_ack", "protocol_version": "1", "task_id": task_id}
        )
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4002


def test_replaced_session_cannot_ack_or_write_result_but_current_can_replay(
    client: TestClient, app
) -> None:
    owner = account(client, "current-session-result-fence-owner")
    enrolled = enroll(
        client, owner["token"], payload=code26_payload(ready=True, state="ready")
    )
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)
    created = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json=task_request("current-session-result-fence-001"),
        headers=headers,
    )
    assert created.status_code == 201
    task_id = created.json()["task"]["id"]

    first_session = app.state.store.open_transport_session(device_id, 2)
    first_attempt = app.state.store.prepare_delivery_attempt(
        device_id, task_id, first_session, 2
    )
    assert first_attempt is not None
    with app.state.database.connect() as connection:
        prepared = connection.execute(
            "SELECT sent_at, ack_deadline, outcome FROM task_delivery_attempts WHERE id = ?",
            (first_attempt["id"],),
        ).fetchone()
    assert prepared is not None
    assert dict(prepared) == {
        "sent_at": None,
        "ack_deadline": None,
        "outcome": "prepared",
    }
    assert app.state.store.mark_task_dispatched(
        device_id,
        task_id,
        server_session_id=first_session,
        delivery_attempt_id=first_attempt["id"],
        ack_deadline_seconds=8,
    )
    with app.state.database.connect() as connection:
        sent = connection.execute(
            "SELECT sent_at, ack_deadline, outcome FROM task_delivery_attempts WHERE id = ?",
            (first_attempt["id"],),
        ).fetchone()
    assert sent is not None
    assert sent["sent_at"] is not None
    assert sent["ack_deadline"] > sent["sent_at"]
    assert sent["outcome"] == "sent"
    second_session = app.state.store.open_transport_session(device_id, 2)

    assert app.state.store.acknowledge_delivery_attempt(
        device_id,
        task_id,
        first_session,
        first_attempt["id"],
        require_attempt_id=True,
    ).value == "rejected"
    rejected = app.state.store.complete_task(
        device_id,
        task_id,
        "succeeded",
        {"source": "old-session"},
        None,
        None,
        server_session_id=first_session,
    )
    assert rejected.kind.value == "not_found"

    replayed = app.state.store.complete_task(
        device_id,
        task_id,
        "succeeded",
        {"source": "durable-outbox"},
        None,
        None,
        server_session_id=second_session,
    )
    assert replayed.acknowledged
    stored = app.state.store.get_task(owner["account"]["id"], device_id, task_id)
    assert stored is not None
    assert stored["result"] == {"source": "durable-outbox"}


def test_persisted_ack_deadline_rejects_late_ack_without_timer(
    client: TestClient, app, monkeypatch
) -> None:
    clock = [int(time.time())]
    monkeypatch.setattr(store_module, "now_seconds", lambda: clock[0])
    owner = account(client, "persisted-ack-deadline-owner")
    enrolled = enroll(
        client, owner["token"], payload=code26_payload(ready=True, state="ready")
    )
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)
    created = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json=task_request("persisted-ack-deadline-001"),
        headers=headers,
    )
    assert created.status_code == 201
    task_id = created.json()["task"]["id"]
    session_id = app.state.store.open_transport_session(device_id, 2)

    expired_attempt = app.state.store.prepare_delivery_attempt(
        device_id, task_id, session_id, 2
    )
    assert expired_attempt is not None
    assert app.state.store.mark_task_dispatched(
        device_id,
        task_id,
        server_session_id=session_id,
        delivery_attempt_id=expired_attempt["id"],
        ack_deadline_seconds=1,
    )
    clock[0] += 1
    assert app.state.store.acknowledge_delivery_attempt(
        device_id,
        task_id,
        session_id,
        expired_attempt["id"],
        require_attempt_id=True,
    ).value == "stale"
    after_late_ack = app.state.store.get_task(
        owner["account"]["id"], device_id, task_id
    )
    assert after_late_ack is not None
    assert after_late_ack["status"] == "dispatched"
    with app.state.database.connect() as connection:
        outcome = connection.execute(
            "SELECT outcome FROM task_delivery_attempts WHERE id = ?",
            (expired_attempt["id"],),
        ).fetchone()["outcome"]
    assert outcome == "ack_timeout"

    current_attempt = app.state.store.prepare_delivery_attempt(
        device_id, task_id, session_id, 2
    )
    assert current_attempt is not None
    assert app.state.store.mark_task_dispatched(
        device_id,
        task_id,
        server_session_id=session_id,
        delivery_attempt_id=current_attempt["id"],
        ack_deadline_seconds=2,
    )
    assert app.state.store.acknowledge_delivery_attempt(
        device_id,
        task_id,
        session_id,
        current_attempt["id"],
        require_attempt_id=True,
    ).value == "acknowledged"
    running = app.state.store.get_task(
        owner["account"]["id"], device_id, task_id
    )
    assert running is not None and running["status"] == "running"


def test_device_active_task_limit_is_eight_and_idempotent_retry_still_works(
    client: TestClient, app
) -> None:
    owner = account(client, "active-task-cap-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    first = None
    for index in range(8):
        response = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request(f"active-task-cap-{index}"),
            headers=headers,
        )
        assert response.status_code == 201
        first = first or response
    assert first is not None
    repeated = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json=task_request("active-task-cap-0"),
        headers=headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["task"]["id"] == first.json()["task"]["id"]

    rejected = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json=task_request("active-task-cap-8"),
        headers=headers,
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "device_task_capacity_reached"


def test_offline_queued_task_dispatches_on_same_device_recovery_within_ttl(
    client: TestClient, app, monkeypatch
) -> None:
    clock = [int(time.time())]
    monkeypatch.setattr(app_module, "now_seconds", lambda: clock[0])
    monkeypatch.setattr(store_module, "now_seconds", lambda: clock[0])
    owner = account(client, "queued-recovery-budget-owner")
    enrolled = enroll(
        client, owner["token"], payload=code26_payload(ready=True, state="ready")
    )
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    # Establish and close an earlier durable session. It must never remain a
    # routing candidate after the same device reconnects with a new session.
    old_session_id = app.state.store.open_transport_session(device_id, 2)
    app.state.store.update_task_channel_state(
        old_session_id, device_id, 2, "ready", True, "none"
    )
    app.state.store.close_device_session(
        old_session_id, device_id, "test_old_session_closed"
    )

    created = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json=task_request("queued-recovery-budget-001"),
        headers=headers,
    )
    assert created.status_code == 201
    task_id = created.json()["task"]["id"]
    assert created.json()["task"]["status"] == "queued"
    assert created.json()["delivery"]["dispatch_outcome"] == "bound_offline_queued"

    other_payload = code26_payload(ready=True, state="ready")
    other_payload["installation_id"] = "install-queued-other-device"
    other_payload["display_name"] = "Queued recovery other device"
    other = enroll(client, owner["token"], payload=other_payload)
    with client.websocket_connect("/api/v1/device-channel") as other_socket:
        websocket_transport_auth(
            other_socket,
            other["device"]["id"],
            other["private_key"],
            app_version_code=28,
        )
        other_socket.send_json(task_channel_state())
        assert other_socket.receive_json()["type"] == "task_channel_state_ack"
        with app.state.database.connect() as connection:
            wrong_device_attempts = connection.execute(
                "SELECT COUNT(*) AS count FROM task_delivery_attempts WHERE task_id = ?",
                (task_id,),
            ).fetchone()["count"]
        assert wrong_device_attempts == 0
        still_queued = app.state.store.get_task(
            owner["account"]["id"], device_id, task_id
        )
        assert still_queued is not None and still_queued["status"] == "queued"

    # This is a server queue-wake contract, not proof that Android or an OEM
    # runtime will always recover by this timestamp. Android owns that separate
    # bounded-recovery contract.
    clock[0] += 39
    with client.websocket_connect("/api/v1/device-channel") as socket:
        auth_ok = websocket_transport_auth(
            socket, device_id, enrolled["private_key"], app_version_code=28
        )
        current_session_id = auth_ok["server_session_id"]
        assert current_session_id != old_session_id
        socket.send_json(task_channel_state())
        assert socket.receive_json()["type"] == "task_channel_state_ack"
        dispatched = socket.receive_json()
        assert dispatched["task_id"] == task_id
        attempt_id = dispatched["delivery_attempt_id"]
        socket.send_json(
            {
                "type": "task_ack",
                "protocol_version": "1",
                "task_id": task_id,
                "delivery_attempt_id": attempt_id,
            }
        )
        socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": task_id,
                "status": "succeeded",
                "result": {"recovered_within_budget": True},
            }
        )
        assert socket.receive_json()["type"] == "task_result_ack"
        device = app.state.store.get_device(owner["account"]["id"], device_id)
        assert device is not None
        assert device["current_session_id"] == current_session_id
        with app.state.database.connect() as connection:
            routed = connection.execute(
                "SELECT server_session_id FROM task_delivery_attempts "
                "WHERE task_id = ? ORDER BY prepared_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        assert routed is not None
        assert routed["server_session_id"] == current_session_id

    stored = app.state.store.get_task(owner["account"]["id"], device_id, task_id)
    assert stored is not None and stored["status"] == "succeeded"


def test_task_ttl_below_protocol_minimum_is_rejected(client: TestClient, app) -> None:
    owner = account(client, "task-ttl-budget-owner")
    enrolled = enroll(client, owner["token"])
    response = client.post(
        f"/api/v1/devices/{enrolled['device']['id']}/tasks",
        json={
            "task_type": "health",
            "parameters": {},
            "ttl_seconds": 9,
            "idempotency_key": "task-ttl-budget-001",
        },
        headers=controller_headers(app, owner),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_task"


def test_offline_queued_task_at_ttl_boundary_expires_without_dispatch(
    client: TestClient, app, monkeypatch
) -> None:
    clock = [int(time.time())]
    monkeypatch.setattr(app_module, "now_seconds", lambda: clock[0])
    monkeypatch.setattr(store_module, "now_seconds", lambda: clock[0])
    owner = account(client, "queued-expiry-boundary-owner")
    enrolled = enroll(
        client, owner["token"], payload=code26_payload(ready=True, state="ready")
    )
    device_id = enrolled["device"]["id"]
    created = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json=task_request("queued-expiry-boundary-001"),
        headers=controller_headers(app, owner),
    )
    assert created.status_code == 201
    task_id = created.json()["task"]["id"]

    clock[0] += 60
    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_transport_auth(
            socket, device_id, enrolled["private_key"], app_version_code=28
        )
        socket.send_json(task_channel_state())
        assert socket.receive_json()["type"] == "task_channel_state_ack"

        stored = app.state.store.get_task(
            owner["account"]["id"], device_id, task_id
        )
        assert stored is not None
        assert stored["status"] == "expired"
        with app.state.database.connect() as connection:
            attempts = connection.execute(
                "SELECT COUNT(*) AS count FROM task_delivery_attempts WHERE task_id = ?",
                (task_id,),
            ).fetchone()["count"]
        assert attempts == 0


def test_legacy_client_keeps_the_exact_auth_ok_contract(client: TestClient) -> None:
    owner = account(client, "legacy-recovery-contract")
    enrolled = enroll(client, owner["token"])

    with client.websocket_connect("/api/v1/device-channel") as socket:
        auth_ok = websocket_auth(
            socket,
            enrolled["device"]["id"],
            enrolled["private_key"],
            enrolled["payload"],
        )
        assert set(auth_ok) == {
            "type",
            "protocol_version",
            "device_id",
            "heartbeat_interval_seconds",
            "server_time",
        }


def test_recovery_not_ready_blocks_create_and_historical_task_dispatch(
    client: TestClient, app
) -> None:
    owner = account(client, "recovery-gate-owner")
    ready_payload = recovery_payload(ready=True, state="ready")
    enrolled = enroll(client, owner["token"], payload=ready_payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    queued = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json=task_request("recovery-existing-001"),
        headers=headers,
    )
    assert queued.status_code == 201
    queued_task = queued.json()["task"]
    assert queued_task["status"] == "queued"

    not_ready_payload = recovery_payload(ready=False, state="recovery_pending")
    with client.websocket_connect("/api/v1/device-channel") as socket:
        auth_ok = websocket_auth(
            socket,
            device_id,
            enrolled["private_key"],
            not_ready_payload,
        )
        assert auth_ok["task_recovery_safe_after"] == queued_task["expires_at"]
        assert auth_ok["task_recovery_safe_after"] > auth_ok["server_time"]

        socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": not_ready_payload,
            }
        )
        assert socket.receive_json()["type"] == "heartbeat_ack"
        persisted = app.state.store.get_task(
            owner["account"]["id"], device_id, queued_task["id"]
        )
        assert persisted is not None
        assert persisted["status"] == "queued"

        socket.send_json(
            {
                "type": "task_ack",
                "protocol_version": "1",
                "task_id": queued_task["id"],
            }
        )
        assert socket.receive_json() == {
            "type": "error",
            "protocol_version": "1",
            "code": "task_channel_not_ready",
            "task_id": queued_task["id"],
        }
        socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": queued_task["id"],
                "status": "succeeded",
                "result": {"ok": True, "source": "must-not-be-accepted"},
            }
        )
        assert socket.receive_json() == {
            "type": "error",
            "protocol_version": "1",
            "code": "task_channel_not_ready",
            "task_id": queued_task["id"],
        }
        unchanged = app.state.store.get_task(
            owner["account"]["id"], device_id, queued_task["id"]
        )
        assert unchanged is not None
        assert unchanged["status"] == "queued"
        assert unchanged["result"] is None
        socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": not_ready_payload,
            }
        )
        assert socket.receive_json()["type"] == "heartbeat_ack"

        queued_while_not_ready = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request("recovery-rejected-001"),
            headers=headers,
        )
        assert queued_while_not_ready.status_code == 201
        assert queued_while_not_ready.json()["task"]["status"] == "queued"
        assert queued_while_not_ready.json()["delivery"]["dispatch_outcome"] == (
            "channel_not_ready_queued"
        )

        page_rejected = client.post(
            f"/devices/{device_id}/tasks",
            data={"csrf": headers["X-CSRF-Token"], "task_type": "health"},
            headers={"Cookie": headers["Cookie"]},
        )
        assert page_rejected.status_code == 200

        page = client.get(
            f"/devices/{device_id}", headers={"Cookie": headers["Cookie"]}
        )
        assert page.status_code == 200
        assert "ONLINE_DIAGNOSTIC_ONLY" in page.text
        assert "恢复协议 v2 · 未就绪（recovery_pending）" in page.text
        assert "设备仅诊断在线" in page.text
        assert "运行 health" not in page.text
        assert "手机端无法可靠检测" in page.text
        assert "系统允许 Agent 发布通知" in page.text

def test_recovery_auth_without_existing_tasks_is_immediately_safe(
    client: TestClient, app
) -> None:
    owner = account(client, "recovery-empty-owner")
    payload = recovery_payload(ready=False, state="recovery_pending")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        auth_ok = websocket_auth(
            socket,
            device_id,
            enrolled["private_key"],
            payload,
        )
        assert auth_ok["task_recovery_safe_after"] == auth_ok["server_time"]

        ready_payload = recovery_payload(ready=True, state="ready")
        socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": ready_payload,
            }
        )
        assert socket.receive_json()["type"] == "heartbeat_ack"

        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request("recovery-ready-001"),
            headers=headers,
        )
        assert created.status_code == 201
        dispatched = socket.receive_json()
        assert dispatched["type"] == "task"
        assert dispatched["task_id"] == created.json()["task"]["id"]


def test_server_enforces_safe_after_before_recovery_and_retries_until_ack(
    client: TestClient, app, monkeypatch
) -> None:
    owner = account(client, "recovery-safe-after-owner")
    ready_payload = recovery_payload(ready=True, state="ready")
    enrolled = enroll(client, owner["token"], payload=ready_payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)
    clock = [int(time.time())]
    monkeypatch.setattr(app_module, "now_seconds", lambda: clock[0])
    monkeypatch.setattr(store_module, "now_seconds", lambda: clock[0])

    historical = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json=task_request("recovery-historical-001"),
        headers=headers,
    )
    assert historical.status_code == 201
    historical_task = historical.json()["task"]
    not_ready_payload = recovery_payload(ready=False, state="recovery_pending")

    with client.websocket_connect("/api/v1/device-channel") as early_socket:
        auth_ok = websocket_auth(
            early_socket,
            device_id,
            enrolled["private_key"],
            not_ready_payload,
        )
        safe_after = auth_ok["task_recovery_safe_after"]
        assert safe_after == historical_task["expires_at"]
        early_socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": ready_payload,
            }
        )
        close = early_socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4002

    persisted = app.state.store.get_device(owner["account"]["id"], device_id)
    assert persisted is not None
    assert persisted["task_channel_ready"] is False
    assert persisted["task_store_state"] == "recovery_pending"

    clock[0] = safe_after
    with client.websocket_connect("/api/v1/device-channel") as recovered_socket:
        recovered_auth = websocket_auth(
            recovered_socket,
            device_id,
            enrolled["private_key"],
            not_ready_payload,
        )
        assert recovered_auth["task_recovery_safe_after"] == safe_after
        recovered_socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": ready_payload,
            }
        )
        assert recovered_socket.receive_json()["type"] == "heartbeat_ack"
        expired = app.state.store.get_task(
            owner["account"]["id"], device_id, historical_task["id"]
        )
        assert expired is not None
        assert expired["status"] == "expired"

        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request("recovery-after-safe-001"),
            headers=headers,
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        dispatched = recovered_socket.receive_json()
        assert dispatched["type"] == "task"
        assert dispatched["task_id"] == task_id

        recovered_socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": ready_payload,
            }
        )
        received_types = {
            recovered_socket.receive_json()["type"],
            recovered_socket.receive_json()["type"],
        }
        assert received_types == {"heartbeat_ack", "task"}
        deadline = time.monotonic() + 1.0
        while True:
            with app.state.database.connect() as connection:
                dispatch_events = connection.execute(
                    "SELECT COUNT(*) AS count FROM task_events "
                    "WHERE task_id = ? AND event_type = 'delivery_attempt'",
                    (task_id,),
                ).fetchone()["count"]
            if dispatch_events == 2 or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert dispatch_events == 2


def test_ready_channel_rejects_downgrade_without_overwriting_persisted_state(
    client: TestClient, app
) -> None:
    owner = account(client, "recovery-downgrade-owner")
    ready_payload = recovery_payload(ready=True, state="ready")
    enrolled = enroll(client, owner["token"], payload=ready_payload)
    device_id = enrolled["device"]["id"]

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(socket, device_id, enrolled["private_key"], ready_payload)
        socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": recovery_payload(
                    ready=False, state="recovery_pending"
                ),
            }
        )
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4002
        assert close["reason"] == "authentication_or_protocol_rejected"

    persisted = app.state.store.get_device(owner["account"]["id"], device_id)
    assert persisted is not None
    assert persisted["task_store_protocol"] == 2
    assert persisted["task_channel_ready"] is True
    assert persisted["task_store_state"] == "ready"


def test_replaced_socket_cannot_overwrite_new_not_ready_capabilities(
    client: TestClient, app, monkeypatch
) -> None:
    owner = account(client, "recovery-replacement-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)
    not_ready_payload = recovery_payload(ready=False, state="recovery_pending")
    auth_send_started = threading.Event()
    release_auth_ok = threading.Event()
    original_send_json = DeviceConnection.send_json

    async def delayed_auth_ok(self, payload):
        if payload.get("type") == "auth_ok" and self.task_store_protocol == 2:
            auth_send_started.set()
            await asyncio.to_thread(release_auth_ok.wait, 2)
        await original_send_json(self, payload)

    monkeypatch.setattr(DeviceConnection, "send_json", delayed_auth_ok)

    with client.websocket_connect("/api/v1/device-channel") as old_socket:
        websocket_auth(
            old_socket,
            device_id,
            enrolled["private_key"],
            enrolled["payload"],
        )
        with client.websocket_connect("/api/v1/device-channel") as new_socket:
            challenge = new_socket.receive_json()
            message = websocket_auth_message(
                device_id,
                challenge["challenge_id"],
                challenge["nonce"],
                challenge["issued_at"],
                not_ready_payload,
            )
            new_socket.send_json(
                {
                    "type": "auth",
                    "protocol_version": "1",
                    "challenge_id": challenge["challenge_id"],
                    "device_id": device_id,
                    "signature": sign(enrolled["private_key"], message),
                    "payload": not_ready_payload,
                }
            )
            assert auth_send_started.wait(2)

            old_socket.send_json(
                {
                    "type": "heartbeat",
                    "protocol_version": "1",
                    "payload": enrolled["payload"],
                }
            )
            old_close = old_socket.receive()
            assert old_close["type"] == "websocket.close"
            assert old_close["code"] == 4002

            persisted = app.state.store.get_device(owner["account"]["id"], device_id)
            assert persisted is not None
            assert persisted["task_store_protocol"] == 2
            assert persisted["task_channel_ready"] is False
            assert persisted["task_store_state"] == "recovery_pending"

            queued_while_not_ready = client.post(
                f"/api/v1/devices/{device_id}/tasks",
                json=task_request("replacement-rejected-001"),
                headers=headers,
            )
            assert queued_while_not_ready.status_code == 201
            assert queued_while_not_ready.json()["task"]["status"] == "queued"
            assert queued_while_not_ready.json()["delivery"]["dispatch_outcome"] == (
                "channel_not_ready_queued"
            )

            release_auth_ok.set()
            assert new_socket.receive_json()["type"] == "auth_ok"


def test_code26_minimal_and_code25_legacy_heartbeats_are_both_strict(
    client: TestClient
) -> None:
    code25_owner = account(client, "heartbeat-code25-owner")
    legacy_payload = recovery_payload(ready=True, state="ready")
    code25 = enroll(client, code25_owner["token"], payload=legacy_payload)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(
            socket,
            code25["device"]["id"],
            code25["private_key"],
            legacy_payload,
        )
        socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": legacy_payload,
            }
        )
        assert socket.receive_json()["type"] == "heartbeat_ack"

    code26_owner = account(client, "heartbeat-code26-owner")
    minimal_payload = code26_payload(ready=True, state="ready")
    code26 = enroll(client, code26_owner["token"], payload=minimal_payload)
    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_transport_auth(
            socket,
            code26["device"]["id"],
            code26["private_key"],
        )
        socket.send_json({"type": "heartbeat", "protocol_version": "1"})
        minimal_ack = socket.receive_json()
        assert set(minimal_ack) == {"type", "protocol_version", "server_time"}
        assert minimal_ack["type"] == "heartbeat_ack"
        socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "mixed_field": True,
            }
        )
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4002

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_transport_auth(
            socket,
            code26["device"]["id"],
            code26["private_key"],
        )
        socket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "1",
                "payload": minimal_payload,
            }
        )
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4002


def test_code25_to_code26_upgrade_authenticates_before_any_device_snapshot(
    client: TestClient, app
) -> None:
    owner = account(client, "code26-minimal-auth-upgrade-owner")
    code25_payload = recovery_payload(ready=True, state="ready")
    code25_payload["app_version"] = "0.9.2-v01-dev"
    code25_payload["app_version_code"] = 25
    enrolled = enroll(client, owner["token"], payload=code25_payload)
    device_id = enrolled["device"]["id"]

    # This is the exact cover-upgrade boundary: the server only has code25 device
    # state, while the new process has no code26 DeviceStateCache entry. Transport
    # authentication and minimal heartbeat must complete before any snapshot update.
    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_transport_auth(socket, device_id, enrolled["private_key"])
        before_snapshot = app.state.store.get_device(
            owner["account"]["id"], device_id
        )
        assert before_snapshot is not None
        assert before_snapshot["app_version_code"] == 25
        assert before_snapshot["device_state"] == "stale"
        assert before_snapshot["task_channel_ready"] is False

        for _ in range(3):
            socket.send_json({"type": "heartbeat", "protocol_version": "1"})
            assert socket.receive_json()["type"] == "heartbeat_ack"

        socket.send_json(
            {
                "type": "device_state_update",
                "protocol_version": "1",
                "state": "stale",
                "failure_stage": "device_snapshot_collect",
            }
        )
        assert socket.receive_json()["type"] == "device_state_ack"
        socket.send_json({"type": "heartbeat", "protocol_version": "1"})
        assert socket.receive_json()["type"] == "heartbeat_ack"

        upgraded_payload = code26_payload(ready=False, state="recovery_pending")
        socket.send_json(
            {
                "type": "device_state_update",
                "protocol_version": "1",
                "state": "fresh",
                "failure_stage": "none",
                "payload": upgraded_payload,
            }
        )
        assert socket.receive_json()["type"] == "device_state_ack"
        after_snapshot = app.state.store.get_device(owner["account"]["id"], device_id)
        assert after_snapshot is not None
        assert after_snapshot["app_version_code"] == 26
        assert after_snapshot["device_state"] == "fresh"


def test_auth_contract_rejects_mixed_and_cross_generation_fields(
    client: TestClient
) -> None:
    owner = account(client, "code26-auth-shape-owner")
    code25_payload = recovery_payload(ready=True, state="ready")
    code25_payload["app_version_code"] = 25
    enrolled = enroll(client, owner["token"], payload=code25_payload)
    device_id = enrolled["device"]["id"]
    transport_auth = {
        "contract": "code26_transport_v1",
        "app_version_code": 26,
        "task_store_protocol": 2,
    }

    with client.websocket_connect("/api/v1/device-channel") as socket:
        challenge = socket.receive_json()
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
                "signature": sign(enrolled["private_key"], message),
                "transport_auth": transport_auth,
                "payload": code25_payload,
            }
        )
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4401

    with client.websocket_connect("/api/v1/device-channel") as socket:
        challenge = socket.receive_json()
        invalid_code26_payload = code26_payload(ready=False, state="recovery_pending")
        message = websocket_auth_message(
            device_id,
            challenge["challenge_id"],
            challenge["nonce"],
            challenge["issued_at"],
            invalid_code26_payload,
        )
        socket.send_json(
            {
                "type": "auth",
                "protocol_version": "1",
                "challenge_id": challenge["challenge_id"],
                "device_id": device_id,
                "signature": sign(enrolled["private_key"], message),
                "payload": invalid_code26_payload,
            }
        )
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4401


def test_task_channel_can_downgrade_without_killing_transport_and_recover_once(
    client: TestClient, app
) -> None:
    owner = account(client, "code26-task-channel-owner")
    payload = code26_payload(ready=True, state="ready")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_transport_auth(socket, device_id, enrolled["private_key"])
        # Even a cached code26 auth payload that says ready is fail-closed until the
        # independent task worker publishes its current state.
        socket.send_json({"type": "heartbeat", "protocol_version": "1"})
        assert socket.receive_json()["type"] == "heartbeat_ack"
        initially_closed = app.state.store.get_device(
            owner["account"]["id"], device_id
        )
        assert initially_closed is not None
        assert initially_closed["task_channel_ready"] is False

        socket.send_json(
            {
                "type": "task_channel_state",
                "protocol_version": "1",
                "task_store_protocol": 2,
                "state": "unavailable",
                "ready": False,
                "failure_stage": "outbox_load",
            }
        )
        not_ready_ack = socket.receive_json()
        assert not_ready_ack["type"] == "task_channel_state_ack"
        assert not_ready_ack["ready"] is False

        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json=task_request("code26-not-ready-queued-001"),
            headers=headers,
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        assert created.json()["delivery"]["dispatch_outcome"] == (
            "channel_not_ready_queued"
        )

        persisted = app.state.store.get_task(owner["account"]["id"], device_id, task_id)
        assert persisted is not None and persisted["status"] == "queued"

        socket.send_json({"type": "heartbeat", "protocol_version": "1"})
        assert socket.receive_json()["type"] == "heartbeat_ack"

        socket.send_json(
            {
                "type": "task_channel_state",
                "protocol_version": "1",
                "task_store_protocol": 2,
                "state": "ready",
                "ready": True,
                "failure_stage": "none",
            }
        )
        ready_ack = socket.receive_json()
        assert ready_ack["type"] == "task_channel_state_ack"
        dispatched = socket.receive_json()
        assert dispatched["type"] == "task"
        assert dispatched["task_id"] == task_id

        deadline = time.monotonic() + 1.0
        while True:
            with app.state.database.connect() as connection:
                dispatch_events = connection.execute(
                    "SELECT COUNT(*) AS count FROM task_events "
                    "WHERE task_id = ? AND event_type = 'delivery_prepared'",
                    (task_id,),
                ).fetchone()["count"]
            if dispatch_events == 1 or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert dispatch_events == 1


def test_device_state_stale_does_not_stop_minimal_transport_heartbeat(
    client: TestClient, app
) -> None:
    owner = account(client, "code26-device-state-owner")
    payload = code26_payload(ready=True, state="ready")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_transport_auth(socket, device_id, enrolled["private_key"])
        socket.send_json(
            {
                "type": "device_state_update",
                "protocol_version": "1",
                "state": "stale",
                "failure_stage": "device_snapshot_collect",
            }
        )
        assert socket.receive_json()["type"] == "device_state_ack"
        stale = app.state.store.get_device(owner["account"]["id"], device_id)
        assert stale is not None
        assert stale["device_state"] == "stale"
        assert stale["device_state_failure_stage"] == "device_snapshot_collect"

        for _ in range(3):
            socket.send_json({"type": "heartbeat", "protocol_version": "1"})
            assert socket.receive_json()["type"] == "heartbeat_ack"

        socket.send_json(
            {
                "type": "device_state_update",
                "protocol_version": "1",
                "state": "fresh",
                "failure_stage": "none",
                "payload": payload,
            }
        )
        assert socket.receive_json()["type"] == "device_state_ack"
        fresh = app.state.store.get_device(owner["account"]["id"], device_id)
        assert fresh is not None
        assert fresh["device_state"] == "fresh"

        socket.send_json(
            {
                "type": "device_state_update",
                "protocol_version": "1",
                "state": "stale",
                "failure_stage": "device_state_cache_store",
            }
        )
        assert socket.receive_json()["type"] == "device_state_ack"
        cache_unavailable = app.state.store.get_device(
            owner["account"]["id"], device_id
        )
        assert cache_unavailable is not None
        assert cache_unavailable["device_state"] == "stale"
        assert cache_unavailable["device_state_failure_stage"] == (
            "device_state_cache_store"
        )
        socket.send_json({"type": "heartbeat", "protocol_version": "1"})
        assert socket.receive_json()["type"] == "heartbeat_ack"
