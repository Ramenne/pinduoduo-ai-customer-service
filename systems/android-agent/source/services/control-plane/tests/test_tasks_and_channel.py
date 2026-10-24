from __future__ import annotations

import asyncio
import copy
import time
from unittest.mock import ANY, AsyncMock, Mock

from fastapi.testclient import TestClient

from conftest import account, controller_headers, device_payload, enroll, sign, websocket_auth
from oin_control_plane.app import (
    TaskDispatchOutcome,
    _dispatch_if_fresh,
    _task_delivery_contract,
    _websocket_disconnect_reason,
    create_app,
)
from oin_control_plane.hub import DeviceConnection, DeviceHub, FreshSendResult
from oin_control_plane.runtime_io import RuntimeIoLane
from oin_control_plane.security import websocket_auth_message


def _payload_with_diagnostics(name: str) -> dict:
    payload = device_payload(name)
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
    payload["diagnostics"] = {
        "process_instance_id": "11111111-1111-4111-8111-111111111111",
        "process_started_at_ms": 1_723_200_000_000,
        "process_pid": 2468,
        "service_instance_id": "22222222-2222-4222-8222-222222222222",
        "service_started_at_ms": 1_723_200_001_000,
        "last_heartbeat_sent_at_ms": 1_723_200_025_000,
        "last_heartbeat_ack_at_ms": 1_723_200_026_000,
        "supervisor_lag_ms": 37,
        "lifecycle_state": "ONLINE_FRESH",
        "last_retire_reason": "heartbeat_ack_grace_exhausted",
        "reconnect_attempt": 0,
        "reconnect_deadline_at_ms": 0,
        "socket_epoch": 17,
    }
    return payload


def test_task_delivery_contract_does_not_infer_offline_from_state_race() -> None:
    task = {"device_id": "device-explicit", "status": "running"}

    delivery = _task_delivery_contract(
        task, TaskDispatchOutcome.TASK_NOT_DISPATCHABLE
    )

    assert delivery == {
        "device_id": "device-explicit",
        "binding_state": "bound",
        "realtime_channel_state": "not_rechecked",
        "task_state": "running",
        "dispatch_outcome": "task_not_dispatchable",
    }


def test_send_failure_is_indeterminate_and_does_not_claim_device_offline() -> None:
    task = {
        "device_id": "device-send-failed",
        "id": "task-send-failed",
        "protocol_version": "1",
        "task_type": "health",
        "issued_at": 1,
        "expires_at": 60,
        "parameters": {},
        "status": "queued",
    }
    store = Mock()
    store.device_task_channel_ready.return_value = True
    store.fresh_device_session_id.return_value = "session-current"
    hub = Mock()
    hub.delivery_protocol = AsyncMock(return_value=1)
    store.prepare_delivery_attempt.return_value = {
        "id": "attempt-send-failed",
        "delivery_protocol": 1,
    }
    hub.send_if_fresh = AsyncMock(return_value=FreshSendResult.FAILED)

    async def dispatch() -> TaskDispatchOutcome:
        runtime_io = RuntimeIoLane(max_workers=1, max_in_flight=2)
        try:
            return await _dispatch_if_fresh(store, hub, runtime_io, task, 50)
        finally:
            runtime_io.close()

    outcome = asyncio.run(dispatch())

    assert outcome is TaskDispatchOutcome.SEND_FAILED_QUEUED
    store.mark_device_offline_if_stale.assert_not_called()
    assert _task_delivery_contract(
        {"device_id": task["device_id"], "status": "queued"}, outcome
    )["realtime_channel_state"] == "send_failed"


def test_database_and_hub_session_mismatch_keeps_task_queued() -> None:
    task = {
        "device_id": "device-session-fence",
        "id": "task-session-fence",
        "protocol_version": "1",
        "task_type": "health",
        "issued_at": 1,
        "expires_at": 60,
        "parameters": {},
        "status": "queued",
    }
    store = Mock()
    store.device_task_channel_ready.return_value = True
    store.fresh_device_session_id.return_value = "session-new"
    hub = Mock()
    hub.delivery_protocol = AsyncMock(return_value=1)
    store.prepare_delivery_attempt.return_value = {
        "id": "attempt-session-mismatch",
        "delivery_protocol": 1,
    }
    hub.send_if_fresh = AsyncMock(return_value=FreshSendResult.SESSION_MISMATCH)

    async def dispatch() -> TaskDispatchOutcome:
        runtime_io = RuntimeIoLane(max_workers=1, max_in_flight=2)
        try:
            return await _dispatch_if_fresh(store, hub, runtime_io, task, 50)
        finally:
            runtime_io.close()

    outcome = asyncio.run(dispatch())

    assert outcome is TaskDispatchOutcome.SESSION_MISMATCH_QUEUED
    hub.send_if_fresh.assert_awaited_once_with(
        "device-session-fence",
        "session-new",
        ANY,
        50,
    )
    store.mark_task_dispatched.assert_not_called()
    store.mark_device_offline_if_stale.assert_not_called()
    assert _task_delivery_contract(task, outcome) == {
        "device_id": "device-session-fence",
        "binding_state": "bound",
        "realtime_channel_state": "session_mismatch",
        "task_state": "queued",
        "dispatch_outcome": "session_mismatch_queued",
    }


def test_hub_never_sends_to_a_different_database_session() -> None:
    class RecordingSocket:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.payloads.append(payload)

        async def close(self, *, code: int, reason: str) -> None:
            return None

    async def scenario() -> tuple[FreshSendResult, list[dict]]:
        socket = RecordingSocket()
        hub = DeviceHub()
        await hub.register(
            DeviceConnection(
                "device-session-fence",
                socket,  # type: ignore[arg-type]
                session_id="session-old",
            )
        )
        result = await hub.send_if_fresh(
            "device-session-fence",
            "session-new",
            {"type": "task"},
            50,
        )
        return result, socket.payloads

    result, payloads = asyncio.run(scenario())
    assert result is FreshSendResult.SESSION_MISMATCH
    assert payloads == []


def test_ack_lease_invalidates_current_even_when_socket_send_never_returns() -> None:
    class HangingSocket:
        def __init__(self) -> None:
            self.send_started = asyncio.Event()
            self.closed = asyncio.Event()
            self.close_code: int | None = None

        async def send_json(self, payload: dict) -> None:
            self.send_started.set()
            await asyncio.Event().wait()

        async def close(self, *, code: int, reason: str) -> None:
            self.close_code = code
            self.closed.set()

    async def scenario() -> FreshSendResult:
        socket = HangingSocket()
        hub = DeviceHub()
        connection = DeviceConnection(
            "device-hung-send",
            socket,  # type: ignore[arg-type]
            session_id="session-hung-send",
            app_version_code=28,
            send_timeout_seconds=2,
            close_timeout_seconds=1,
        )
        await hub.register(connection)

        async def lease_expired(
            expired_connection: DeviceConnection,
            task_id: str,
            attempt_id: str,
        ) -> None:
            if not expired_connection.is_fresh(2):
                await hub.close_if_current(
                    expired_connection,
                    code=4008,
                    reason="transport_client_activity_stale",
                )

        await hub.start_delivery_lease(
            connection,
            task_id="task-hung-send",
            attempt_id="attempt-hung-send",
            ack_deadline_seconds=1,
            on_expire=lease_expired,
        )
        sending = asyncio.create_task(
            hub.send_if_fresh(
                connection.device_id,
                connection.session_id,
                {"type": "task"},
                50,
            )
        )
        await asyncio.wait_for(socket.send_started.wait(), timeout=0.2)
        connection.last_client_activity_monotonic = time.monotonic() - 3
        await asyncio.wait_for(socket.closed.wait(), timeout=1.5)
        assert socket.close_code == 4008
        assert not await hub.is_current(connection)
        return await asyncio.wait_for(sending, timeout=1.5)

    assert asyncio.run(scenario()) is FreshSendResult.FAILED


def test_register_does_not_wait_for_old_hung_send_and_late_send_is_fenced() -> None:
    class ControlledSocket:
        def __init__(self, *, hang: bool) -> None:
            self.hang = hang
            self.send_started = asyncio.Event()
            self.release_send = asyncio.Event()
            self.payloads: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.send_started.set()
            if self.hang:
                await self.release_send.wait()
            self.payloads.append(payload)

        async def close(self, *, code: int, reason: str) -> None:
            return None

    async def scenario() -> tuple[FreshSendResult, list[dict]]:
        hub = DeviceHub()
        old_socket = ControlledSocket(hang=True)
        old_connection = DeviceConnection(
            "device-replaced-send",
            old_socket,  # type: ignore[arg-type]
            session_id="session-old",
            send_timeout_seconds=5,
            close_timeout_seconds=1,
        )
        await hub.register(old_connection)
        old_send = asyncio.create_task(
            hub.send_if_fresh(
                old_connection.device_id,
                old_connection.session_id,
                {"type": "task", "epoch": "old"},
                50,
            )
        )
        await asyncio.wait_for(old_socket.send_started.wait(), timeout=0.2)

        new_socket = ControlledSocket(hang=False)
        new_connection = DeviceConnection(
            "device-replaced-send",
            new_socket,  # type: ignore[arg-type]
            session_id="session-new",
            send_timeout_seconds=1,
            close_timeout_seconds=1,
        )
        await asyncio.wait_for(hub.register(new_connection), timeout=0.2)
        assert await hub.is_current(new_connection)
        await new_connection.send_json({"type": "heartbeat_ack", "epoch": "new"})

        old_socket.release_send.set()
        old_outcome = await asyncio.wait_for(old_send, timeout=0.5)
        assert await hub.is_current(new_connection)
        return old_outcome, new_socket.payloads

    outcome, new_payloads = asyncio.run(scenario())
    assert outcome is FreshSendResult.SESSION_MISMATCH
    assert new_payloads == [{"type": "heartbeat_ack", "epoch": "new"}]


def test_task_allowlist_idempotency_and_cross_account_rejection(client: TestClient, app) -> None:
    owner = account(client, "task-owner")
    stranger = account(client, "task-stranger")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    owner_headers = controller_headers(app, owner)

    unknown = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={
            "task_type": "shell",
            "parameters": {},
            "ttl_seconds": 60,
            "idempotency_key": "unknown-task-001",
        },
        headers=owner_headers,
    )
    assert unknown.status_code == 422

    arbitrary_parameters = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={
            "task_type": "observe",
            "parameters": {"url": "https://not-allowed.invalid"},
            "ttl_seconds": 60,
            "idempotency_key": "bad-params-001",
        },
        headers=owner_headers,
    )
    assert arbitrary_parameters.status_code == 422

    disallowed_package = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={
            "task_type": "action",
            "parameters": {"action": "launch_app", "package": "com.example.other"},
            "ttl_seconds": 60,
            "idempotency_key": "bad-action-package-001",
        },
        headers=owner_headers,
    )
    assert disallowed_package.status_code == 422

    allowed_action = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={
            "task_type": "action",
            "parameters": {"action": "launch_app", "package": "com.taobao.idlefish"},
            "ttl_seconds": 60,
            "idempotency_key": "launch-xianyu-action-001",
        },
        headers=owner_headers,
    )
    assert allowed_action.status_code == 201
    assert allowed_action.json()["task"]["parameters"] == {
        "action": "launch_app",
        "package": "com.taobao.idlefish",
    }

    allowed_click = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={
            "task_type": "action",
            "parameters": {
                "action": "click_node",
                "package": "com.taobao.idlefish",
                "selector": {"kind": "text", "value": "消息"},
            },
            "ttl_seconds": 60,
            "idempotency_key": "click-xianyu-message-tab-001",
        },
        headers=owner_headers,
    )
    assert allowed_click.status_code == 201
    assert allowed_click.json()["task"]["parameters"] == {
        "action": "click_node",
        "package": "com.taobao.idlefish",
        "selector": {"kind": "text", "value": "消息"},
    }

    allowed_scroll = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={
            "task_type": "action",
            "parameters": {
                "action": "scroll_page",
                "package": "com.taobao.idlefish",
                "direction": "up",
            },
            "ttl_seconds": 60,
            "idempotency_key": "scroll-xianyu-page-up-001",
        },
        headers=owner_headers,
    )
    assert allowed_scroll.status_code == 201
    assert allowed_scroll.json()["task"]["parameters"] == {
        "action": "scroll_page",
        "package": "com.taobao.idlefish",
        "direction": "up",
    }

    for suffix, parameters in (
        (
            "wrong-package",
            {
                "action": "click_node",
                "package": "com.example.other",
                "selector": {"kind": "text", "value": "消息"},
            },
        ),
        (
            "selector-extra",
            {
                "action": "click_node",
                "package": "com.taobao.idlefish",
                "selector": {"kind": "text", "value": "消息", "contains": True},
            },
        ),
        (
            "selector-kind",
            {
                "action": "click_node",
                "package": "com.taobao.idlefish",
                "selector": {"kind": "bounds", "value": "0,0,1,1"},
            },
        ),
        (
            "selector-blank",
            {
                "action": "click_node",
                "package": "com.taobao.idlefish",
                "selector": {"kind": "text", "value": "   "},
            },
        ),
        (
            "scroll-wrong-package",
            {
                "action": "scroll_page",
                "package": "com.example.other",
                "direction": "up",
            },
        ),
        (
            "scroll-direction",
            {
                "action": "scroll_page",
                "package": "com.taobao.idlefish",
                "direction": "left",
            },
        ),
        (
            "scroll-extra",
            {
                "action": "scroll_page",
                "package": "com.taobao.idlefish",
                "direction": "down",
                "distance": 900,
            },
        ),
    ):
        rejected_click = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json={
                "task_type": "action",
                "parameters": parameters,
                "ttl_seconds": 60,
                "idempotency_key": f"bad-click-{suffix}-001",
            },
            headers=owner_headers,
        )
        assert rejected_click.status_code == 422

    request = {
        "task_type": "health",
        "parameters": {},
        "ttl_seconds": 60,
        "idempotency_key": "health-idempotent-001",
    }
    first = client.post(
        f"/api/v1/devices/{device_id}/tasks", json=request, headers=owner_headers
    )
    second = client.post(
        f"/api/v1/devices/{device_id}/tasks", json=request, headers=owner_headers
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["task"]["id"] == second.json()["task"]["id"]
    assert second.json()["created"] is False

    conflict = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={**request, "task_type": "observe"},
        headers=owner_headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    cross_account = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={**request, "idempotency_key": "stranger-try-001"},
        headers=controller_headers(app, stranger),
    )
    assert cross_account.status_code == 404


def test_task_type_must_be_advertised_by_installed_agent(client: TestClient, app) -> None:
    owner = account(client, "old-agent-owner")
    payload = device_payload("Old agent")
    payload["capabilities"]["cloud_task_types"] = [
        "health",
        "observe",
        "xianyu_snapshot",
    ]
    enrolled = enroll(client, owner["token"], payload=payload)
    headers = controller_headers(app, owner)

    rejected = client.post(
        f"/api/v1/devices/{enrolled['device']['id']}/tasks",
        json={
            "task_type": "action",
            "parameters": {"action": "launch_app", "package": "com.taobao.idlefish"},
            "ttl_seconds": 60,
            "idempotency_key": "unsupported-agent-action-001",
        },
        headers=headers,
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "device_task_type_unsupported"


def test_authenticated_channel_heartbeat_task_result_and_reconnect(client: TestClient, app) -> None:
    owner = account(client, "channel-owner")
    enrolled = enroll(client, owner["token"])
    device = enrolled["device"]
    private_key = enrolled["private_key"]
    payload = enrolled["payload"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        authenticated = websocket_auth(socket, device["id"], private_key, payload)
        assert authenticated["device_id"] == device["id"]

        socket.send_json(
            {"type": "heartbeat", "protocol_version": "1", "payload": payload}
        )
        assert socket.receive_json()["type"] == "heartbeat_ack"
        device_page = client.get(
            f"/devices/{device['id']}", headers={"Cookie": headers["Cookie"]}
        )
        assert device_page.status_code == 200
        assert "启动闲鱼（受限动作）" in device_page.text

        created = client.post(
            f"/api/v1/devices/{device['id']}/tasks",
            json={
                "task_type": "health",
                "parameters": {},
                "ttl_seconds": 60,
                "idempotency_key": "live-health-001",
            },
            headers=headers,
        )
        assert created.status_code == 201
        assert created.json()["delivery"] == {
            "device_id": device["id"],
            "binding_state": "bound",
            "realtime_channel_state": "dispatchable",
            "task_state": "dispatched",
            "dispatch_outcome": "dispatched",
        }
        task_id = created.json()["task"]["id"]
        task = socket.receive_json()
        assert task["type"] == "task"
        assert task["task_id"] == task_id
        assert task["task_type"] == "health"

        socket.send_json(
            {"type": "task_ack", "protocol_version": "1", "task_id": task_id}
        )
        socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": task_id,
                "status": "succeeded",
                "result": {"ok": True, "source": "test-device"},
            }
        )
        acknowledgement = socket.receive_json()
        assert acknowledgement == {
            "type": "task_result_ack",
            "protocol_version": "1",
            "task_id": task_id,
        }
        completed = client.get(
            f"/api/v1/devices/{device['id']}/tasks/{task_id}", headers=headers
        )
        assert completed.json()["task"]["status"] == "succeeded"

        socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": task_id,
                "status": "succeeded",
                "result": {"ok": False, "source": "conflicting-replay"},
            }
        )
        conflict = socket.receive_json()
        assert conflict["type"] == "error"
        assert conflict["code"] == "task_result_conflict"

        socket.send_json(
            {
                "type": "task_result",
                "protocol_version": "1",
                "task_id": task_id,
                "status": "succeeded",
                "result": {"ok": True, "source": "test-device"},
            }
        )
        assert socket.receive_json()["type"] == "task_result_ack"

    with client.websocket_connect("/api/v1/device-channel") as reconnected:
        websocket_auth(reconnected, device["id"], private_key, payload)
        reconnected.send_json(
            {"type": "heartbeat", "protocol_version": "1", "payload": payload}
        )
        assert reconnected.receive_json()["type"] == "heartbeat_ack"


def test_signed_diagnostics_are_persisted_and_visible_to_the_owner(
    client: TestClient, app
) -> None:
    owner = account(client, "diagnostics-owner")
    payload = _payload_with_diagnostics("Diagnostics phone")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(socket, device_id, enrolled["private_key"], payload)
        heartbeat_payload = copy.deepcopy(payload)
        heartbeat_payload["diagnostics"].update(
            {
                "last_heartbeat_sent_at_ms": 1_723_200_030_000,
                "last_heartbeat_ack_at_ms": 1_723_200_029_000,
                "supervisor_lag_ms": 51,
                "reconnect_attempt": 2,
                "reconnect_deadline_at_ms": 1_723_200_060_000,
                "socket_epoch": 18,
            }
        )
        socket.send_json(
            {"type": "heartbeat", "protocol_version": "1", "payload": heartbeat_payload}
        )
        assert socket.receive_json()["type"] == "heartbeat_ack"

        device = client.get(
            f"/api/v1/devices/{device_id}", headers=headers
        ).json()["device"]
        assert device["diagnostics"] == heartbeat_payload["diagnostics"]
        page = client.get(f"/devices/{device_id}", headers=headers)
        assert page.status_code == 200
        assert "11111111-1111-4111-8111-111111111111" in page.text
        assert "ONLINE_FRESH" in page.text
        assert "heartbeat_ack_grace_exhausted" in page.text
        assert "18 / 2" in page.text


def test_code29_recovery_diagnostics_are_visible_without_becoming_realtime_authority(
    client: TestClient, app
) -> None:
    owner = account(client, "code29-recovery-diagnostics-owner")
    payload = _payload_with_diagnostics("Code29 recovery diagnostics")
    payload["app_version"] = "0.9.6-v01"
    payload["app_version_code"] = 29
    payload["diagnostics"].update(
        {
            "transport_failure_stage": "none",
            "device_state_failure_stage": "none",
            "outbox_failure_stage": "none",
            "server_session_id": "unknown",
            "service_watchdog_lag_ms": 187_385,
            "max_supervisor_lag_ms": 187_385,
            "max_service_watchdog_lag_ms": 187_385,
            "last_recovery_started_at_ms": 1_723_200_030_000,
            "last_recovery_completed_at_ms": 1_723_200_035_000,
            "last_recovery_attempts": 2,
            "last_recovery_trigger": "heartbeat_ack_suspect",
            "last_recovery_failure_stage": "websocket_upgrade",
            "last_recovery_outcome": "recovered",
        }
    )
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    device = client.get(
        f"/api/v1/devices/{device_id}", headers=headers
    ).json()["device"]
    assert device["online"] is False
    assert device["diagnostics"]["last_recovery_outcome"] == "recovered"
    page = client.get(f"/devices/{device_id}", headers=headers)
    assert page.status_code == 200
    assert "187385 / 187385 ms" in page.text
    assert "heartbeat_ack_suspect" in page.text
    assert "websocket_upgrade" in page.text
    assert "recovered · attempts 2" in page.text


def test_mobile_diagnostics_cannot_override_authoritative_offline_state_or_queue_age(
    client: TestClient, app, monkeypatch
) -> None:
    clock = [int(time.time())]
    monkeypatch.setattr("oin_control_plane.store.now_seconds", lambda: clock[0])
    owner = account(client, "diagnostics-authority-offline-owner")
    payload = _payload_with_diagnostics("Diagnostics authority phone")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)
    created = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={
            "task_type": "health",
            "parameters": {},
            "ttl_seconds": 60,
            "idempotency_key": "diagnostic-authority-offline-001",
        },
        headers=headers,
    )
    assert created.status_code == 201

    device = client.get(
        f"/api/v1/devices/{device_id}", headers=headers
    ).json()["device"]
    assert device["diagnostics"]["lifecycle_state"] == "ONLINE_FRESH"
    assert device["online"] is False
    assert device["state"] == "BOUND_OFFLINE"
    assert device["current_session_id"] is None
    assert device["pending_task_count"] == 1
    assert device["oldest_pending_task_age_seconds"] >= 0
    assert 0 < device["nearest_pending_task_ttl_seconds"] <= 60

    clock[0] += 60
    expired = client.get(
        f"/api/v1/devices/{device_id}", headers=headers
    ).json()["device"]
    assert expired["pending_task_count"] == 0
    assert expired["oldest_pending_task_age_seconds"] is None
    assert expired["nearest_pending_task_ttl_seconds"] is None


def test_stale_open_session_is_not_exposed_as_current_realtime_authority(
    client: TestClient, app, monkeypatch
) -> None:
    clock = [int(time.time())]
    monkeypatch.setattr("oin_control_plane.store.now_seconds", lambda: clock[0])
    owner = account(client, "stale-open-session-authority-owner")
    payload = _payload_with_diagnostics("Stale open session phone")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(socket, device_id, enrolled["private_key"], payload)
        clock[0] += app.state.settings.dispatch_freshness_seconds + 1
        stale = client.get(
            f"/api/v1/devices/{device_id}", headers=headers
        ).json()["device"]
        assert stale["diagnostics"]["lifecycle_state"] == "ONLINE_FRESH"
        assert stale["online"] is False
        assert stale["current_session_id"] is None
        assert stale["offline_duration_seconds"] >= 3


def test_server_session_is_authoritative_and_reconnect_gap_is_observable(
    client: TestClient, app, monkeypatch
) -> None:
    clock = [int(time.time())]
    monkeypatch.setattr("oin_control_plane.store.now_seconds", lambda: clock[0])
    owner = account(client, "diagnostics-authority-session-owner")
    payload = _payload_with_diagnostics("Diagnostics session phone")
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as first:
        websocket_auth(first, device_id, enrolled["private_key"], payload)
        first_device = client.get(
            f"/api/v1/devices/{device_id}", headers=headers
        ).json()["device"]
        first_session_id = first_device["current_session_id"]
        assert first_device["online"] is True
        assert first_session_id is not None

    offline = client.get(
        f"/api/v1/devices/{device_id}", headers=headers
    ).json()["device"]
    assert offline["diagnostics"]["lifecycle_state"] == "ONLINE_FRESH"
    assert offline["online"] is False
    assert offline["current_session_id"] is None

    clock[0] += 2
    with client.websocket_connect("/api/v1/device-channel") as second:
        websocket_auth(second, device_id, enrolled["private_key"], payload)
        reconnected = client.get(
            f"/api/v1/devices/{device_id}", headers=headers
        ).json()["device"]
        assert reconnected["online"] is True
        assert reconnected["current_session_id"] not in {None, first_session_id}
        assert reconnected["last_reconnect_gap_seconds"] >= 1


def test_fresh_database_session_without_hub_socket_is_not_effectively_online(
    client: TestClient, app
) -> None:
    owner = account(client, "db-fresh-hub-absent-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    session_id = app.state.store.open_device_session(
        device_id, enrolled["payload"]
    )
    raw = app.state.store.get_device(owner["account"]["id"], device_id)
    assert raw is not None
    assert raw["online"] is True
    assert raw["current_session_id"] == session_id

    public = client.get(
        f"/api/v1/devices/{device_id}",
        headers=controller_headers(app, owner),
    ).json()["device"]
    assert public["db_session_fresh"] is True
    assert public["hub_session_current"] is False
    assert public["online"] is False
    assert public["current_session_id"] is None
    assert public["state"] == "BOUND_OFFLINE"
    assert "realtime_socket_absent" in public["status_reasons"]


def test_stale_socket_keeps_new_task_queued_until_fresh_reconnect(
    client: TestClient, app
) -> None:
    owner = account(client, "stale-dispatch-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as stale_socket:
        websocket_auth(
            stale_socket, device_id, enrolled["private_key"], enrolled["payload"]
        )
        time.sleep(app.state.settings.dispatch_freshness_seconds + 0.05)
        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json={
                "task_type": "health",
                "parameters": {},
                "ttl_seconds": 60,
                "idempotency_key": "stale-dispatch-001",
            },
            headers=headers,
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        assert created.json()["task"]["status"] == "queued"
        assert created.json()["delivery"] == {
            "device_id": device_id,
            "binding_state": "bound",
            "realtime_channel_state": "offline",
            "task_state": "queued",
            "dispatch_outcome": "bound_offline_queued",
        }
        assert app.state.store.get_device(owner["account"]["id"], device_id)["online"] is False
        close = stale_socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4008
        assert close["reason"] == "stale_client_activity"

    with client.websocket_connect("/api/v1/device-channel") as fresh_socket:
        websocket_auth(
            fresh_socket, device_id, enrolled["private_key"], enrolled["payload"]
        )
        dispatched = fresh_socket.receive_json()
        assert dispatched["type"] == "task"
        assert dispatched["task_id"] == task_id
        deadline = time.monotonic() + 1.0
        while True:
            refreshed = client.get(
                f"/api/v1/devices/{device_id}/tasks/{task_id}", headers=headers
            )
            if (
                refreshed.json()["task"]["status"] == "dispatched"
                or time.monotonic() >= deadline
            ):
                break
            time.sleep(0.01)
        assert refreshed.json()["task"]["status"] == "dispatched"


def test_heartbeat_redelivers_dispatched_task_until_phone_ack(
    client: TestClient, app
) -> None:
    owner = account(client, "redelivery-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    payload = enrolled["payload"]
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(socket, device_id, enrolled["private_key"], payload)
        socket.send_json({"type": "heartbeat", "protocol_version": "1", "payload": payload})
        assert socket.receive_json()["type"] == "heartbeat_ack"

        created = client.post(
            f"/api/v1/devices/{device_id}/tasks",
            json={
                "task_type": "health",
                "parameters": {},
                "ttl_seconds": 60,
                "idempotency_key": "heartbeat-redelivery-001",
            },
            headers=headers,
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        first_delivery = socket.receive_json()
        assert first_delivery["task_id"] == task_id

        # Simulate a task envelope lost after server send but before the phone
        # persisted its claim/ACK. The next client activity proves the socket and
        # redelivers the same task instead of leaving it stuck as dispatched.
        socket.send_json({"type": "heartbeat", "protocol_version": "1", "payload": payload})
        messages = [socket.receive_json(), socket.receive_json()]
        assert {message["type"] for message in messages} == {"heartbeat_ack", "task"}
        redelivery = next(message for message in messages if message["type"] == "task")
        assert redelivery == first_delivery

        socket.send_json(
            {"type": "task_ack", "protocol_version": "1", "task_id": task_id}
        )
        deadline = time.monotonic() + 1.0
        while True:
            refreshed = client.get(
                f"/api/v1/devices/{device_id}/tasks/{task_id}", headers=headers
            )
            if (
                refreshed.json()["task"]["status"] == "running"
                or time.monotonic() >= deadline
            ):
                break
            time.sleep(0.01)
        assert refreshed.json()["task"]["status"] == "running"


def test_websocket_auth_signature_covers_the_exact_sent_payload(client: TestClient) -> None:
    owner = account(client, "ws-payload-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    signed_payload = enrolled["payload"]
    sent_payload = copy.deepcopy(signed_payload)
    sent_payload["display_name"] = "Tampered after signing"

    with client.websocket_connect("/api/v1/device-channel") as socket:
        challenge = socket.receive_json()
        assert challenge["algorithm"] == "ECDSA_P256_SHA256_DER"
        assert challenge["signature_encoding"] == "ASN1_DER"
        message = websocket_auth_message(
            device_id,
            challenge["challenge_id"],
            challenge["nonce"],
            challenge["issued_at"],
            signed_payload,
        )
        socket.send_json(
            {
                "type": "auth",
                "protocol_version": "1",
                "challenge_id": challenge["challenge_id"],
                "device_id": device_id,
                "signature": sign(enrolled["private_key"], message),
                "payload": sent_payload,
            }
        )
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4401


def test_new_socket_replaces_old_without_old_close_marking_device_offline(
    client: TestClient, app
) -> None:
    owner = account(client, "socket-race-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]

    with client.websocket_connect("/api/v1/device-channel") as old_socket:
        websocket_auth(
            old_socket, device_id, enrolled["private_key"], enrolled["payload"]
        )
        with client.websocket_connect("/api/v1/device-channel") as new_socket:
            websocket_auth(
                new_socket, device_id, enrolled["private_key"], enrolled["payload"]
            )
            old_close = old_socket.receive()
            assert old_close["type"] == "websocket.close"
            assert old_close["code"] == 4009
            deadline = time.monotonic() + 1.0
            while True:
                with app.state.database.connect() as connection:
                    open_sessions = connection.execute(
                        "SELECT COUNT(*) AS count FROM device_sessions "
                        "WHERE device_id = ? AND disconnected_at IS NULL",
                        (device_id,),
                    ).fetchone()["count"]
                    online = connection.execute(
                        "SELECT online FROM devices WHERE id = ?", (device_id,)
                    ).fetchone()["online"]
                if open_sessions == 1 or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            assert open_sessions == 1
            assert online == 1

    deadline = time.monotonic() + 2.0
    while True:
        with app.state.database.connect() as connection:
            open_sessions = connection.execute(
                "SELECT COUNT(*) AS count FROM device_sessions "
                "WHERE device_id = ? AND disconnected_at IS NULL",
                (device_id,),
            ).fetchone()["count"]
            online = connection.execute(
                "SELECT online FROM devices WHERE id = ?", (device_id,)
            ).fetchone()["online"]
        if open_sessions == 0 or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert open_sessions == 0
    assert online == 0


def test_same_account_two_online_devices_route_tasks_by_explicit_device_id(
    client: TestClient, app
) -> None:
    owner = account(client, "two-device-owner")
    first_payload = device_payload("Owner phone A")
    first_payload["installation_id"] = "install-owner-phone-a"
    second_payload = device_payload("Owner phone B")
    second_payload["installation_id"] = "install-owner-phone-b"
    first = enroll(client, owner["token"], payload=first_payload)
    second = enroll(client, owner["token"], payload=second_payload)
    headers = controller_headers(app, owner)

    with client.websocket_connect("/api/v1/device-channel") as first_socket:
        websocket_auth(
            first_socket, first["device"]["id"], first["private_key"], first_payload
        )
        with client.websocket_connect("/api/v1/device-channel") as second_socket:
            websocket_auth(
                second_socket,
                second["device"]["id"],
                second["private_key"],
                second_payload,
            )

            first_created = client.post(
                f"/api/v1/devices/{first['device']['id']}/tasks",
                json={
                    "task_type": "health",
                    "parameters": {},
                    "ttl_seconds": 60,
                    "idempotency_key": "owner-phone-a-health-001",
                },
                headers=headers,
            )
            second_created = client.post(
                f"/api/v1/devices/{second['device']['id']}/tasks",
                json={
                    "task_type": "health",
                    "parameters": {},
                    "ttl_seconds": 60,
                    "idempotency_key": "owner-phone-b-health-001",
                },
                headers=headers,
            )
            first_task = first_socket.receive_json()
            second_task = second_socket.receive_json()

            assert first_created.json()["task"]["device_id"] == first["device"]["id"]
            assert second_created.json()["task"]["device_id"] == second["device"]["id"]
            assert first_created.json()["delivery"]["dispatch_outcome"] == "dispatched"
            assert second_created.json()["delivery"]["dispatch_outcome"] == "dispatched"
            assert first_task["task_id"] == first_created.json()["task"]["id"]
            assert second_task["task_id"] == second_created.json()["task"]["id"]


def test_two_accounts_online_devices_remain_owner_and_socket_isolated(
    client: TestClient, app
) -> None:
    first_owner = account(client, "routing-owner-a")
    second_owner = account(client, "routing-owner-b")
    first_payload = device_payload("Account A phone")
    first_payload["installation_id"] = "install-account-a-phone"
    second_payload = device_payload("Account B phone")
    second_payload["installation_id"] = "install-account-b-phone"
    first = enroll(client, first_owner["token"], payload=first_payload)
    second = enroll(client, second_owner["token"], payload=second_payload)
    first_headers = controller_headers(app, first_owner)
    second_headers = controller_headers(app, second_owner)

    with client.websocket_connect("/api/v1/device-channel") as first_socket:
        websocket_auth(
            first_socket, first["device"]["id"], first["private_key"], first_payload
        )
        with client.websocket_connect("/api/v1/device-channel") as second_socket:
            websocket_auth(
                second_socket,
                second["device"]["id"],
                second["private_key"],
                second_payload,
            )

            cross_account = client.post(
                f"/api/v1/devices/{second['device']['id']}/tasks",
                json={
                    "task_type": "health",
                    "parameters": {},
                    "ttl_seconds": 60,
                    "idempotency_key": "cross-account-route-001",
                },
                headers=first_headers,
            )
            assert cross_account.status_code == 404

            first_created = client.post(
                f"/api/v1/devices/{first['device']['id']}/tasks",
                json={
                    "task_type": "health",
                    "parameters": {},
                    "ttl_seconds": 60,
                    "idempotency_key": "account-a-health-001",
                },
                headers=first_headers,
            )
            second_created = client.post(
                f"/api/v1/devices/{second['device']['id']}/tasks",
                json={
                    "task_type": "health",
                    "parameters": {},
                    "ttl_seconds": 60,
                    "idempotency_key": "account-b-health-001",
                },
                headers=second_headers,
            )
            assert first_socket.receive_json()["task_id"] == first_created.json()["task"]["id"]
            assert second_socket.receive_json()["task_id"] == second_created.json()["task"]["id"]


def test_client_reconnect_close_reason_is_preserved_without_arbitrary_text(
    client: TestClient, app
) -> None:
    owner = account(client, "close-reason-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]

    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(
            socket, device_id, enrolled["private_key"], enrolled["payload"]
        )
        socket.close(code=4007, reason="heartbeat_ack_grace_exhausted")
        # The production socket transports its close frame independently. Give
        # TestClient's in-process transport one scheduler turn before teardown.
        time.sleep(0.05)

    deadline = time.monotonic() + 1.0
    while True:
        with app.state.database.connect() as connection:
            session = connection.execute(
                "SELECT disconnect_reason FROM device_sessions "
                "WHERE device_id = ? ORDER BY connected_at DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
        if (
            session["disconnect_reason"]
            == "client_reconnect:heartbeat_ack_grace_exhausted"
            or time.monotonic() >= deadline
        ):
            break
        time.sleep(0.01)
    assert session["disconnect_reason"] == (
        "client_reconnect:heartbeat_ack_grace_exhausted"
    )
    persisted_device = app.state.store.get_device(owner["account"]["id"], device_id)
    assert persisted_device["binding_state"] == "bound"
    assert persisted_device["online"] is False
    assert _websocket_disconnect_reason(4007, "secret-looking-arbitrary-text") == (
        "peer_disconnected_code_4007"
    )


def test_revoke_invalidates_device_credential(client: TestClient, app) -> None:
    owner = account(client, "revoke-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)
    with client.websocket_connect("/api/v1/device-channel") as socket:
        websocket_auth(socket, device_id, enrolled["private_key"], enrolled["payload"])
        revoked = client.post(f"/api/v1/devices/{device_id}/revoke", headers=headers)
        assert revoked.status_code == 200
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4403
        assert close["reason"] == "device_revoked"
    assert app.state.store.get_device_credential(device_id) is None
    device = client.get(f"/api/v1/devices/{device_id}", headers=headers).json()["device"]
    assert device["state"] == "REVOKED"


def test_offline_revoked_device_receives_terminal_revoked_close(client: TestClient, app) -> None:
    owner = account(client, "offline-revoke-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)
    assert client.post(f"/api/v1/devices/{device_id}/revoke", headers=headers).status_code == 200

    with client.websocket_connect("/api/v1/device-channel") as socket:
        challenge = socket.receive_json()
        socket.send_json(
            {
                "type": "auth",
                "protocol_version": "1",
                "challenge_id": challenge["challenge_id"],
                "device_id": device_id,
                "signature": sign(
                    enrolled["private_key"],
                    websocket_auth_message(
                        device_id,
                        challenge["challenge_id"],
                        challenge["nonce"],
                        challenge["issued_at"],
                        enrolled["payload"],
                    ),
                ),
                "payload": enrolled["payload"],
            }
        )
        close = socket.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4403
        assert close["reason"] == "device_revoked"


def test_control_plane_restart_reconciles_persisted_online_state(client: TestClient, app) -> None:
    owner = account(client, "restart-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    session_id = app.state.store.open_device_session(device_id, enrolled["payload"])
    assert session_id
    assert app.state.store.get_device(owner["account"]["id"], device_id)["online"] is True

    restarted = create_app(app.state.settings)
    reconciled = restarted.state.store.get_device(owner["account"]["id"], device_id)
    assert reconciled["online"] is False
    with restarted.state.database.connect() as connection:
        session = connection.execute(
            "SELECT disconnected_at, disconnect_reason FROM device_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    assert session["disconnected_at"] is not None
    assert session["disconnect_reason"] == "control_plane_restarted"


def test_expired_task_is_never_dispatched(client: TestClient, app) -> None:
    owner = account(client, "expiry-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    headers = controller_headers(app, owner)
    created = client.post(
        f"/api/v1/devices/{device_id}/tasks",
        json={
            "task_type": "observe",
            "parameters": {},
            "ttl_seconds": 60,
            "idempotency_key": "expiry-check-001",
        },
        headers=headers,
    )
    task_id = created.json()["task"]["id"]
    with app.state.database.connect() as connection:
        connection.execute(
            "UPDATE device_tasks SET expires_at = 1 WHERE id = ?",
            (task_id,),
        )
    expired = app.state.store.get_task(owner["account"]["id"], device_id, task_id)
    assert expired is not None
    assert expired["status"] == "expired"
    assert expired["error"]["code"] == "task_expired"
