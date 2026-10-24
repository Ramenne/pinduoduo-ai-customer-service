from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket


class FreshSendResult(str, Enum):
    SENT = "sent"
    OFFLINE = "offline"
    NOT_READY = "not_ready"
    STALE = "stale"
    SESSION_MISMATCH = "session_mismatch"
    FAILED = "failed"


@dataclass(slots=True)
class DeliveryLease:
    task_id: str
    attempt_id: str
    timer: asyncio.Task[None]


@dataclass(eq=False, slots=True)
class DeviceConnection:
    device_id: str
    websocket: WebSocket
    session_id: str = ""
    task_store_protocol: int | None = None
    task_channel_ready: bool = True
    task_recovery_safe_after: int | None = None
    independent_lanes: bool = False
    app_version_code: int = 0
    send_timeout_seconds: float = 3.0
    close_timeout_seconds: float = 2.0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    lease_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_client_activity_monotonic: float = field(default_factory=time.monotonic)
    delivery_leases: dict[str, dict[str, DeliveryLease]] = field(default_factory=dict)

    def mark_client_activity(self) -> None:
        self.last_client_activity_monotonic = time.monotonic()

    def is_fresh(self, maximum_idle_seconds: int) -> bool:
        return time.monotonic() - self.last_client_activity_monotonic < maximum_idle_seconds

    async def send_json(self, payload: dict[str, Any]) -> None:
        async def serialized_send() -> None:
            async with self.send_lock:
                await self.websocket.send_json(payload)

        try:
            await asyncio.wait_for(
                serialized_send(), timeout=self.send_timeout_seconds
            )
        except asyncio.TimeoutError as timeout:
            raise RuntimeError("websocket_send_timeout") from timeout
        except asyncio.CancelledError:
            raise
        except Exception as failure:
            raise RuntimeError("websocket_send_failed") from failure

    async def set_task_channel_ready(self, ready: bool) -> None:
        async with self.state_lock:
            self.task_channel_ready = ready

    async def mark_task_channel_ready(self) -> None:
        await self.set_task_channel_ready(True)

    async def send_task_if_ready_and_fresh(
        self,
        payload: dict[str, Any],
        maximum_idle_seconds: int,
    ) -> FreshSendResult:
        async with self.state_lock:
            if not self.task_channel_ready:
                return FreshSendResult.NOT_READY
            if not self.is_fresh(maximum_idle_seconds):
                return FreshSendResult.STALE
        await self.send_json(payload)
        return FreshSendResult.SENT

    async def start_delivery_lease(
        self,
        *,
        task_id: str,
        attempt_id: str,
        ack_deadline_seconds: int,
        on_expire: Callable[["DeviceConnection", str, str], Awaitable[None]],
    ) -> None:
        async def expire() -> None:
            try:
                await asyncio.sleep(ack_deadline_seconds)
                async with self.lease_lock:
                    attempts = self.delivery_leases.get(task_id)
                    lease = attempts.get(attempt_id) if attempts else None
                    if lease is None:
                        return
                    del attempts[attempt_id]
                    if not attempts:
                        self.delivery_leases.pop(task_id, None)
                await on_expire(self, task_id, attempt_id)
            except asyncio.CancelledError:
                return

        timer = asyncio.create_task(expire())
        lease = DeliveryLease(
            task_id,
            attempt_id,
            timer,
        )
        async with self.lease_lock:
            self.delivery_leases.setdefault(task_id, {})[attempt_id] = lease

    async def acknowledge_task(self, task_id: str, attempt_id: str | None) -> bool:
        async with self.lease_lock:
            attempts = self.delivery_leases.get(task_id)
            if not attempts:
                return False
            if self.app_version_code >= 28:
                if not attempt_id or attempt_id not in attempts:
                    return False
            elif attempt_id is not None:
                return False
            leases = list(attempts.values())
            self.delivery_leases.pop(task_id, None)
            for lease in leases:
                lease.timer.cancel()
            return True

    async def cancel_delivery_attempt(self, task_id: str, attempt_id: str) -> bool:
        async with self.lease_lock:
            attempts = self.delivery_leases.get(task_id)
            lease = attempts.pop(attempt_id, None) if attempts else None
            if lease is None:
                return False
            if not attempts:
                self.delivery_leases.pop(task_id, None)
            lease.timer.cancel()
            return True

    async def confirm_task_result(self, task_id: str) -> bool:
        async with self.lease_lock:
            attempts = self.delivery_leases.pop(task_id, None)
            if not attempts:
                return False
            for lease in attempts.values():
                lease.timer.cancel()
            return True

    async def cancel_delivery_leases(self) -> None:
        async with self.lease_lock:
            leases = [
                lease
                for attempts in self.delivery_leases.values()
                for lease in attempts.values()
            ]
            self.delivery_leases.clear()
            for lease in leases:
                lease.timer.cancel()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self.cancel_delivery_leases()
        try:
            await asyncio.wait_for(
                self.websocket.close(code=code, reason=reason[:120]),
                timeout=self.close_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


class DeviceHub:
    def __init__(self) -> None:
        self._connections: dict[str, DeviceConnection] = {}
        self._lock = asyncio.Lock()
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

    def _close_in_background(
        self, connection: DeviceConnection, *, code: int, reason: str
    ) -> None:
        cleanup = asyncio.create_task(connection.close(code=code, reason=reason))
        self._cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._cleanup_tasks.discard)

    async def register(self, connection: DeviceConnection) -> None:
        previous: DeviceConnection | None
        async with self._lock:
            previous = self._connections.get(connection.device_id)
            self._connections[connection.device_id] = connection
        if previous is not None and previous is not connection:
            self._close_in_background(
                previous, code=4009, reason="replaced_by_new_device_session"
            )

    async def unregister(self, connection: DeviceConnection) -> bool:
        async with self._lock:
            if self._connections.get(connection.device_id) is connection:
                del self._connections[connection.device_id]
                return True
            return False

    async def send_if_fresh(
        self,
        device_id: str,
        expected_session_id: str,
        payload: dict[str, Any],
        maximum_idle_seconds: int,
    ) -> FreshSendResult:
        async with self._lock:
            connection = self._connections.get(device_id)
        if connection is None:
            return FreshSendResult.OFFLINE
        if not expected_session_id or connection.session_id != expected_session_id:
            return FreshSendResult.SESSION_MISMATCH
        try:
            outcome = await connection.send_task_if_ready_and_fresh(
                payload,
                maximum_idle_seconds,
            )
        except RuntimeError:
            async with self._lock:
                if self._connections.get(device_id) is connection:
                    del self._connections[device_id]
                    self._close_in_background(
                        connection, code=1011, reason="websocket_send_failed"
                    )
            return FreshSendResult.FAILED
        if outcome is FreshSendResult.SENT:
            async with self._lock:
                current = self._connections.get(device_id)
            if current is not connection or connection.session_id != expected_session_id:
                return FreshSendResult.SESSION_MISMATCH
            return FreshSendResult.SENT
        if outcome is FreshSendResult.NOT_READY:
            return FreshSendResult.NOT_READY
        async with self._lock:
            if (
                self._connections.get(device_id) is connection
                and not connection.is_fresh(maximum_idle_seconds)
            ):
                del self._connections[device_id]
                expired = True
            else:
                expired = False
        if expired:
            self._close_in_background(
                connection, code=4008, reason="stale_client_activity"
            )
            return FreshSendResult.STALE
        return FreshSendResult.FAILED

    async def start_delivery_lease(
        self,
        connection: DeviceConnection,
        *,
        task_id: str,
        attempt_id: str,
        ack_deadline_seconds: int,
        on_expire: Callable[[DeviceConnection, str, str], Awaitable[None]],
    ) -> bool:
        if not await self.is_current(connection):
            return False
        await connection.start_delivery_lease(
            task_id=task_id,
            attempt_id=attempt_id,
            ack_deadline_seconds=ack_deadline_seconds,
            on_expire=on_expire,
        )
        if not await self.is_current(connection):
            await connection.acknowledge_task(task_id, attempt_id)
            return False
        return True

    async def current_connection(
        self, device_id: str, expected_session_id: str
    ) -> DeviceConnection | None:
        async with self._lock:
            connection = self._connections.get(device_id)
            if connection is None or connection.session_id != expected_session_id:
                return None
            return connection

    async def delivery_protocol(
        self, device_id: str, expected_session_id: str
    ) -> int | None:
        async with self._lock:
            connection = self._connections.get(device_id)
            if connection is None or connection.session_id != expected_session_id:
                return None
            return 2 if connection.app_version_code >= 28 else 1

    async def is_current(self, connection: DeviceConnection) -> bool:
        async with self._lock:
            return self._connections.get(connection.device_id) is connection

    async def acknowledge_task(
        self, connection: DeviceConnection, task_id: str, attempt_id: str | None
    ) -> bool:
        if not await self.is_current(connection):
            return False
        return await connection.acknowledge_task(task_id, attempt_id)

    async def cancel_delivery_attempt(
        self, connection: DeviceConnection, task_id: str, attempt_id: str
    ) -> bool:
        return await connection.cancel_delivery_attempt(task_id, attempt_id)

    async def confirm_task_result(
        self, connection: DeviceConnection, task_id: str
    ) -> bool:
        if not await self.is_current(connection):
            return False
        return await connection.confirm_task_result(task_id)

    async def close_if_current(
        self, connection: DeviceConnection, *, code: int, reason: str
    ) -> bool:
        async with self._lock:
            if self._connections.get(connection.device_id) is not connection:
                return False
            del self._connections[connection.device_id]
        self._close_in_background(connection, code=code, reason=reason)
        return True

    async def expire_if_stale(self, device_id: str, maximum_idle_seconds: int) -> bool:
        async with self._lock:
            connection = self._connections.get(device_id)
            if connection is None or connection.is_fresh(maximum_idle_seconds):
                return False
            del self._connections[device_id]
        self._close_in_background(
            connection, code=4008, reason="stale_client_activity"
        )
        return True

    async def disconnect(self, device_id: str, *, code: int, reason: str) -> bool:
        async with self._lock:
            connection = self._connections.pop(device_id, None)
        if connection is None:
            return False
        self._close_in_background(connection, code=code, reason=reason)
        return True

    async def is_online(self, device_id: str) -> bool:
        async with self._lock:
            return device_id in self._connections

    async def realtime_sessions(
        self, device_ids: list[str]
    ) -> dict[str, str]:
        """Return the process-local current socket epoch for requested devices."""

        requested = set(device_ids)
        async with self._lock:
            return {
                device_id: connection.session_id
                for device_id, connection in self._connections.items()
                if device_id in requested
            }
