from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import Settings
from .database import Database
from .hub import DeviceConnection, DeviceHub, FreshSendResult
from .pages import auth_form, device_page, devices_page, download_page
from .seller import SellerStore, SellerEngine
from .seller_routes import install_seller_routes
from .protocol import (
    DEFAULT_TASK_TTL_SECONDS,
    PROTOCOL_VERSION,
    task_channel_ready as payload_task_channel_ready,
    task_store_protocol,
    validate_device_state_update,
    validate_device_payload,
    validate_heartbeat_message,
    validate_task_channel_state,
    validate_task_request,
    validate_transport_auth,
)
from .runtime_io import RuntimeIoBusy, RuntimeIoLane, RuntimeIoTimeout
from .operations_v1 import (
    B4IdentityStore,
    PDD_IDENTITY_INGRESS_MAX_BYTES,
    OperationsAuthentication,
    OperationsConflict,
    OperationsForbidden,
    OperationsNotFound,
    validate_route_identifier,
)
from .security import (
    SecurityError,
    b64url_encode,
    canonical_jcs_bytes,
    canonical_json,
    canonical_payload_digest,
    load_p256_public_key,
    new_opaque_token,
    normalize_identifier,
    now_seconds,
    safe_text,
    verify_ecdsa_signature,
    websocket_auth_message,
    websocket_transport_auth_message,
)
from .store import (
    AuthenticationError,
    ConflictError,
    DeliveryAckKind,
    NotFoundError,
    Session,
    Store,
    TaskCompletionKind,
    TaskChannelNotReadyError,
    TaskCapacityError,
    UnsupportedDeviceTaskError,
)


WEB_COOKIE = "oin_session"
PREAUTH_COOKIE = "oin_preauth"
PRESERVED_CLIENT_CLOSE_REASONS = frozenset(
    {
        "service_stopped",
        "manual_reconnect",
        "network_changed",
        "network_unavailable",
        "cloud_liveness_probe_unavailable",
        "heartbeat_ack_grace_exhausted",
        "cloud_auth_timeout",
        "heartbeat_failed",
        "task_ack_send_rejected",
        "pending_task_result_delivery_reconnect",
        "task_result_delivery_reconnect",
        "task_result_resend_reconnect",
        "task_executor_rejection_delivery_reconnect",
        "task_rejection_replay_reconnect",
        "task_rejection_delivery_reconnect",
        "task_rejection_send_failed",
        "outbox_reconciliation_delivery_reconnect",
        "cloud_protocol_rejected",
        "device_identity_unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class AuthContext:
    account: dict[str, Any]
    web_session: Session | None
    bearer_token: str | None


class TaskDispatchOutcome(str, Enum):
    DISPATCHED = "dispatched"
    BOUND_OFFLINE_QUEUED = "bound_offline_queued"
    CHANNEL_NOT_READY_QUEUED = "channel_not_ready_queued"
    SEND_FAILED_QUEUED = "send_failed_queued"
    SESSION_MISMATCH_QUEUED = "session_mismatch_queued"
    TASK_NOT_DISPATCHABLE = "task_not_dispatchable"
    EXISTING_NOT_RECHECKED = "existing_not_rechecked"


class LoginLimiter:
    def __init__(self, maximum_failures: int = 5, window_seconds: int = 300):
        self.maximum_failures = maximum_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[int]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(remote_hint: str, identifier: str) -> str:
        value = f"{remote_hint}|{normalize_identifier(identifier)}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def blocked(self, key: str) -> bool:
        current = now_seconds()
        with self._lock:
            recent = [item for item in self._failures.get(key, []) if item > current - self.window_seconds]
            self._failures[key] = recent
            return len(recent) >= self.maximum_failures

    def fail(self, key: str) -> None:
        with self._lock:
            self._failures.setdefault(key, []).append(now_seconds())

    def succeed(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def _json_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _operations_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, OperationsNotFound):
        return _json_error(404, exc.code, "The requested identity route was not found")
    if isinstance(exc, OperationsConflict):
        return _json_error(409, exc.code, str(exc))
    if isinstance(exc, OperationsAuthentication):
        return _json_error(401, exc.code, "Connector enrollment or proof was rejected")
    if isinstance(exc, SecurityError):
        return _json_error(400, "validation_error", str(exc))
    raise exc


def _k2_error_response(exc: Exception, *, canonical: bool = False) -> Response:
    detail_code = str(exc)
    if not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", detail_code):
        detail_code = "request_rejected"
    if isinstance(exc, OperationsAuthentication):
        status, code = 401, "PROOF_INVALID"
    elif isinstance(exc, OperationsForbidden):
        status, code = 403, "FORBIDDEN"
    elif isinstance(exc, OperationsNotFound):
        status, code = 404, "DEPENDENCY_CONFLICT"
    elif isinstance(exc, OperationsConflict):
        status = 409
        if "idempotency" in detail_code or "reuse" in detail_code:
            code = "IDEMPOTENCY_CONFLICT"
        elif "identity" in detail_code and "conflict" in detail_code:
            code = "IDENTITY_EVIDENCE_MISMATCH"
        elif "revision" in detail_code or "stale" in detail_code or "fence" in detail_code:
            code = "REVISION_CONFLICT"
        else:
            code = "STATE_CONFLICT"
    elif isinstance(exc, SecurityError):
        status, code = 400, "VALIDATION_ERROR"
    else:
        raise exc
    payload = {
        "error": {
            "code": code,
            "retryable": False,
            "detail_code": detail_code,
        }
    }
    if canonical:
        return Response(
            content=canonical_jcs_bytes(payload),
            media_type="application/json",
            status_code=status,
        )
    return JSONResponse(payload, status_code=status)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SecurityError("duplicate_json_field")
        value[key] = item
    return value


def _bounded_json_integer(raw: str) -> int:
    if len(raw.lstrip("-")) > 19:
        raise SecurityError("json_integer_out_of_range")
    value = int(raw)
    if value < -(2**63) or value > 2**63 - 1:
        raise SecurityError("json_integer_out_of_range")
    return value


def _reject_json_constant(_: str) -> None:
    raise SecurityError("invalid_json_number")


def _reject_json_float(_: str) -> None:
    raise SecurityError("json_numbers_must_be_integers")


def _strict_json_loads(raw: str) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=_unique_json_object,
        parse_int=_bounded_json_integer,
        parse_float=_reject_json_float,
        parse_constant=_reject_json_constant,
    )


async def _bounded_body(request: Request, maximum: int) -> bytes:
    collected = bytearray()
    async for chunk in request.stream():
        if len(collected) + len(chunk) > maximum:
            raise SecurityError("request_body_too_large")
        collected.extend(chunk)
    return bytes(collected)


async def _json_body(request: Request, *, maximum: int = 256 * 1024) -> dict[str, Any]:
    raw = await _bounded_body(request, maximum)
    try:
        value = _strict_json_loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SecurityError("invalid_json") from exc
    if not isinstance(value, dict):
        raise SecurityError("json_body_must_be_object")
    return value


async def _jcs_json_body(request: Request, *, maximum: int) -> dict[str, Any]:
    """Read one bounded runtime-v2 frame and require its exact canonical bytes."""

    raw = await _bounded_body(request, maximum)
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SecurityError("runtime_json_bom_forbidden")
    try:
        value = _strict_json_loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SecurityError("invalid_runtime_json") from exc
    if not isinstance(value, dict):
        raise SecurityError("runtime_json_body_must_be_object")
    if canonical_jcs_bytes(value) != raw:
        raise SecurityError("runtime_json_not_canonical")
    return value


async def _form_body(request: Request, *, maximum: int = 32 * 1024) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise SecurityError("invalid_form_content_type")
    raw = await _bounded_body(request, maximum)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeError as exc:
        raise SecurityError("invalid_form_encoding") from exc
    parsed = parse_qs(decoded, keep_blank_values=True, strict_parsing=False, max_num_fields=20)
    if any(len(values) != 1 for values in parsed.values()):
        raise SecurityError("duplicate_form_field")
    return {key: values[0] for key, values in parsed.items()}


def _bearer(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer "):
        return None
    token = value[7:].strip()
    return token if token and len(token) <= 512 else None


async def _mobile_context(
    request: Request, store: Store, runtime_io: RuntimeIoLane
) -> AuthContext | None:
    bearer = _bearer(request)
    if bearer:
        account = await runtime_io.call(store.get_mobile_account, bearer)
        if account:
            return AuthContext(account=account, web_session=None, bearer_token=bearer)
    return None


async def _web_context(
    request: Request, store: Store, runtime_io: RuntimeIoLane
) -> AuthContext | None:
    session = await runtime_io.call(
        store.get_web_session, request.cookies.get(WEB_COOKIE)
    )
    if session:
        return AuthContext(account=session.account, web_session=session, bearer_token=None)
    return None


def _csrf_valid(request: Request, context: AuthContext, supplied: str | None = None) -> bool:
    if context.web_session is None:
        return True
    candidate = supplied if supplied is not None else request.headers.get("x-csrf-token")
    if not candidate:
        return False
    import hmac

    return hmac.compare_digest(context.web_session.csrf_token, candidate)


def _set_cookie(response: Response, name: str, value: str, settings: Settings, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="lax",
        path=settings.cookie_path,
    )


def _delete_cookie(response: Response, name: str, settings: Settings) -> None:
    response.delete_cookie(name, path=settings.cookie_path, secure=True, httponly=True, samesite="lax")


def _redirect(settings: Settings, path: str) -> RedirectResponse:
    return RedirectResponse(settings.browser_path(path), status_code=303)


def _artifact_info(settings: Settings) -> tuple[Path | None, str | None]:
    path = settings.apk_path
    if path is None or not path.is_file() or path.suffix.lower() != ".apk":
        return None, None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return path, digest.hexdigest()


def _task_wire(
    task: dict[str, Any], delivery_attempt_id: str | None = None
) -> dict[str, Any]:
    wire = {
        "type": "task",
        "protocol_version": task["protocol_version"],
        "task_id": task["id"],
        "task_type": task["task_type"],
        "issued_at": task["issued_at"],
        "expires_at": task["expires_at"],
        "parameters": task["parameters"],
    }
    if delivery_attempt_id is not None:
        wire["delivery_attempt_id"] = delivery_attempt_id
    return wire


async def _dispatch_if_fresh(
    store: Store,
    hub: DeviceHub,
    runtime_io: RuntimeIoLane,
    task: dict[str, Any],
    maximum_idle_seconds: int,
) -> TaskDispatchOutcome:
    device_id = task["device_id"]
    if not await runtime_io.call(store.device_task_channel_ready, device_id):
        return TaskDispatchOutcome.CHANNEL_NOT_READY_QUEUED
    session_id = await runtime_io.call(
        store.fresh_device_session_id, device_id, maximum_idle_seconds
    )
    if session_id is None:
        await hub.expire_if_stale(device_id, maximum_idle_seconds)
        await runtime_io.call(
            store.mark_device_offline_if_stale, device_id, maximum_idle_seconds
        )
        return TaskDispatchOutcome.BOUND_OFFLINE_QUEUED
    delivery_protocol = await hub.delivery_protocol(device_id, session_id)
    if delivery_protocol is None:
        return TaskDispatchOutcome.SESSION_MISMATCH_QUEUED
    attempt = await runtime_io.call(
        store.prepare_delivery_attempt,
        device_id,
        task["id"],
        session_id,
        delivery_protocol,
    )
    if attempt is None:
        return TaskDispatchOutcome.TASK_NOT_DISPATCHABLE

    async def expire_attempt(
        connection: DeviceConnection,
        expired_task_id: str,
        attempt_id: str,
    ) -> None:
        timed_out = await runtime_io.call(
            store.fail_delivery_attempt, attempt_id, "ack_timeout"
        )
        if timed_out and not connection.is_fresh(
            store.settings.heartbeat_timeout_seconds
        ):
            await hub.close_if_current(
                connection, code=4008, reason="transport_client_activity_stale"
            )

    outcome = await hub.send_if_fresh(
        device_id,
        session_id,
        _task_wire(task, attempt["id"] if delivery_protocol >= 2 else None),
        maximum_idle_seconds,
    )
    if outcome is FreshSendResult.NOT_READY:
        await runtime_io.call(store.fail_delivery_attempt, attempt["id"], "superseded")
        return TaskDispatchOutcome.CHANNEL_NOT_READY_QUEUED
    if outcome in {FreshSendResult.OFFLINE, FreshSendResult.STALE}:
        await runtime_io.call(store.fail_delivery_attempt, attempt["id"], "send_failed")
        await runtime_io.call(
            store.mark_device_offline_if_stale, device_id, maximum_idle_seconds
        )
        return TaskDispatchOutcome.BOUND_OFFLINE_QUEUED
    if outcome is FreshSendResult.FAILED:
        await runtime_io.call(store.fail_delivery_attempt, attempt["id"], "send_failed")
        return TaskDispatchOutcome.SEND_FAILED_QUEUED
    if outcome is FreshSendResult.SESSION_MISMATCH:
        await runtime_io.call(store.fail_delivery_attempt, attempt["id"], "superseded")
        return TaskDispatchOutcome.SESSION_MISMATCH_QUEUED
    sent = await runtime_io.call(
        store.mark_task_dispatched,
        device_id,
        task["id"],
        server_session_id=session_id,
        delivery_attempt_id=attempt["id"],
        ack_deadline_seconds=store.settings.task_ack_deadline_seconds,
    )
    if sent is None:
        await runtime_io.call(store.fail_delivery_attempt, attempt["id"], "superseded")
        return TaskDispatchOutcome.TASK_NOT_DISPATCHABLE
    if not sent["acknowledged"]:
        connection = await hub.current_connection(device_id, session_id)
        lease_started = bool(
            connection is not None
            and await hub.start_delivery_lease(
                connection,
                task_id=task["id"],
                attempt_id=attempt["id"],
                ack_deadline_seconds=max(
                    1, sent["ack_deadline"] - now_seconds()
                ),
                on_expire=expire_attempt,
            )
        )
        if not lease_started:
            await runtime_io.call(
                store.fail_delivery_attempt, attempt["id"], "superseded"
            )
            return TaskDispatchOutcome.SESSION_MISMATCH_QUEUED
        if not await runtime_io.call(
            store.delivery_attempt_is_pending, attempt["id"]
        ):
            await hub.cancel_delivery_attempt(
                connection, task["id"], attempt["id"]
            )
    return TaskDispatchOutcome.DISPATCHED


def _task_delivery_contract(
    task: dict[str, Any], outcome: TaskDispatchOutcome
) -> dict[str, Any]:
    if outcome is TaskDispatchOutcome.DISPATCHED:
        realtime_state = "dispatchable"
    elif outcome is TaskDispatchOutcome.CHANNEL_NOT_READY_QUEUED:
        realtime_state = "not_ready"
    elif outcome in {
        TaskDispatchOutcome.TASK_NOT_DISPATCHABLE,
        TaskDispatchOutcome.EXISTING_NOT_RECHECKED,
    }:
        realtime_state = "not_rechecked"
    elif outcome is TaskDispatchOutcome.SEND_FAILED_QUEUED:
        realtime_state = "send_failed"
    elif outcome is TaskDispatchOutcome.SESSION_MISMATCH_QUEUED:
        realtime_state = "session_mismatch"
    else:
        realtime_state = "offline"
    return {
        "device_id": task["device_id"],
        "binding_state": "bound",
        "realtime_channel_state": realtime_state,
        "task_state": task["status"],
        "dispatch_outcome": outcome.value,
    }


def _effective_realtime_device(
    device: dict[str, Any], realtime_session_id: str | None
) -> dict[str, Any]:
    """Combine durable DB freshness with this process's current socket epoch."""

    effective = dict(device)
    database_session_id = device.get("current_session_id")
    database_session_fresh = bool(device.get("online") and database_session_id)
    socket_matches = bool(
        realtime_session_id
        and database_session_id
        and realtime_session_id == database_session_id
    )
    effective["db_session_fresh"] = database_session_fresh
    effective["hub_session_current"] = socket_matches
    effective["online"] = socket_matches
    effective["current_session_id"] = database_session_id if socket_matches else None
    if database_session_fresh and not socket_matches:
        effective["state"] = "BOUND_OFFLINE"
        effective["status_reasons"] = list(
            dict.fromkeys([*device.get("status_reasons", []), "realtime_socket_absent"])
        )
    return effective


async def _effective_realtime_devices(
    devices: list[dict[str, Any]], hub: DeviceHub
) -> list[dict[str, Any]]:
    sessions = await hub.realtime_sessions([device["id"] for device in devices])
    return [
        _effective_realtime_device(device, sessions.get(device["id"]))
        for device in devices
    ]


async def _outbox_reconciliation_wire(
    store: Store, runtime_io: RuntimeIoLane, device_id: str
) -> dict[str, Any] | None:
    authorization = await runtime_io.call(
        store.pending_outbox_reconciliation, device_id
    )
    if authorization is None:
        return None
    return {
        "authorization_id": authorization["id"],
        "challenge": authorization["challenge"],
        "old_scope": authorization["old_device_id"],
        "new_scope": authorization["new_device_id"],
        "expires_at": authorization["expires_at"],
    }


def _validate_reconciliation_manifest(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 8:
        raise SecurityError("invalid_reconciliation_manifest")
    normalized: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "task_id",
            "local_state",
            "result_sha256",
            "result_bytes",
        }:
            raise SecurityError("invalid_reconciliation_manifest_item")
        task_id = safe_text(
            raw.get("task_id"), maximum=80, field="task_id", allow_empty=False
        )
        if task_id in task_ids:
            raise SecurityError("duplicate_reconciliation_task")
        task_ids.add(task_id)
        local_state = safe_text(
            raw.get("local_state"), maximum=12, field="local_state", allow_empty=False
        )
        digest = safe_text(
            raw.get("result_sha256"), maximum=64, field="result_sha256"
        ).lower()
        result_bytes = raw.get("result_bytes")
        if not isinstance(result_bytes, int) or isinstance(result_bytes, bool):
            raise SecurityError("invalid_reconciliation_result_bytes")
        if local_state == "RUNNING":
            if digest or result_bytes != 0:
                raise SecurityError("running_reconciliation_has_result")
        elif local_state == "PENDING":
            if (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or result_bytes <= 0
                or result_bytes > 1024 * 1024
            ):
                raise SecurityError("invalid_pending_reconciliation_digest")
        else:
            raise SecurityError("invalid_reconciliation_local_state")
        normalized.append(
            {
                "task_id": task_id,
                "local_state": local_state,
                "result_sha256": digest,
                "result_bytes": result_bytes,
            }
        )
    return normalized


def _validated_task_result(message: dict[str, Any]) -> tuple[str, Any, str | None, str | None]:
    status = safe_text(
        message.get("status"), maximum=20, field="status", allow_empty=False
    )
    error = message.get("error")
    error_code = None
    error_message = None
    if error is not None:
        if not isinstance(error, dict) or set(error) != {"code", "message"}:
            raise SecurityError("invalid_task_error")
        error_code = safe_text(
            error.get("code"), maximum=80, field="error_code", allow_empty=False
        )
        error_message = safe_text(
            error.get("message"), maximum=300, field="error_message", allow_empty=False
        )
    result = message.get("result")
    if status == "succeeded":
        if "result" not in message or error is not None:
            raise SecurityError("succeeded_task_requires_result_without_error")
    elif status in {"failed", "rejected"}:
        if "result" in message or error_code is None or error_message is None:
            raise SecurityError("failed_task_requires_error_without_result")
    else:
        raise SecurityError("invalid_task_result_status")
    return status, result, error_code, error_message


def _websocket_disconnect_reason(code: int | None, reason: str | None) -> str:
    normalized_reason = reason.strip() if isinstance(reason, str) else ""
    if normalized_reason in PRESERVED_CLIENT_CLOSE_REASONS:
        prefix = "client_reconnect" if code == 4007 else "client_closed"
        return f"{prefix}:{normalized_reason}"[:80]
    if isinstance(code, int) and 1000 <= code <= 4999:
        return f"peer_disconnected_code_{code}"
    return "peer_disconnected"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(
        settings.database_path,
        settings.database_url,
        connect_timeout_seconds=settings.database_connect_timeout_seconds,
        statement_timeout_ms=settings.database_statement_timeout_ms,
        lock_timeout_ms=settings.database_lock_timeout_ms,
    )
    if settings.database_auto_migrate:
        database.migrate()
    else:
        database.verify_schema()
    database.verify_runtime_privileges()
    store = Store(database, settings)
    identity_store = B4IdentityStore(database, settings)
    store.reconcile_startup_connections()
    hub = DeviceHub()
    limiter = LoginLimiter()
    runtime_io = RuntimeIoLane()
    cleanup_task: asyncio.Task[None] | None = None
    seller_store = SellerStore(database, store)
    async def seller_connected(device_id: str) -> bool:
        if not await runtime_io.call(store.device_task_channel_ready, device_id):
            return False
        session = await runtime_io.call(store.fresh_device_session_id, device_id, settings.dispatch_freshness_seconds)
        return bool(session and await hub.delivery_protocol(device_id, session) is not None)
    async def seller_dispatch(task):
        return await _dispatch_if_fresh(store, hub, runtime_io, task, settings.dispatch_freshness_seconds)
    seller_engine = SellerEngine(seller_store, runtime_io, seller_dispatch, seller_connected)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        seller_task = asyncio.create_task(seller_engine.run())
        try:
            yield
        finally:
            seller_task.cancel()
            try:
                await seller_task
            except asyncio.CancelledError:
                pass
            if cleanup_task is not None and not cleanup_task.done():
                cleanup_task.cancel()
            runtime_io.close()

    app = FastAPI(
        title="OIN V01 Control Plane",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    if settings.environment == "production":
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.public_base_url).hostname or ""])
    app.state.settings = settings
    app.state.database = database
    app.state.store = store
    app.state.identity_store = identity_store
    app.state.hub = hub
    app.state.runtime_io = runtime_io
    app.state.seller_store = seller_store
    app.state.seller_engine = seller_engine
    install_seller_routes(app, seller_store, runtime_io, _web_context, _csrf_valid, _json_body, settings)

    async def b4_write(request: Request, operation: Any) -> JSONResponse:
        if request.headers.get("authorization"):
            context = await _mobile_context(request, store, runtime_io)
        else:
            context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401,
                "authentication_required",
                "Authenticated controller login is required",
            )
        if not _csrf_valid(request, context):
            return _json_error(403, "csrf_failed", "CSRF validation failed")
        try:
            body = await _json_body(request, maximum=16 * 1024)
            outcome = await runtime_io.call(
                operation,
                context.account["id"],
                context.account["user_id"],
                body,
            )
        except (SecurityError, OperationsNotFound, OperationsConflict, OperationsAuthentication) as exc:
            return _operations_error_response(exc)
        payload = {
            "disposition": "idempotent_replay" if outcome.replayed else "applied",
            **outcome.value,
        }
        return JSONResponse(payload, status_code=200 if outcome.replayed else 201)

    async def b4_runtime_command(request: Request, operation: Any) -> Response:
        try:
            body = await _jcs_json_body(request, maximum=262_144)
            result = await runtime_io.call(operation, body)
        except (SecurityError, OperationsNotFound, OperationsConflict, OperationsAuthentication) as exc:
            error_response = _operations_error_response(exc)
            error_payload = _strict_json_loads(error_response.body.decode("utf-8"))
            return Response(
                content=canonical_jcs_bytes(error_payload),
                media_type="application/json",
                status_code=error_response.status_code,
            )
        return Response(
            content=canonical_jcs_bytes(result),
            media_type="application/json",
            status_code=201,
        )

    async def run_cleanup() -> None:
        try:
            await runtime_io.call(store.maybe_cleanup_expired)
        except (RuntimeIoBusy, RuntimeIoTimeout, OSError, RuntimeError):
            return

    def schedule_cleanup() -> None:
        nonlocal cleanup_task
        if cleanup_task is None or cleanup_task.done():
            cleanup_task = asyncio.create_task(run_cleanup())

    async def runtime_io_unavailable(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            {"error": "runtime_dependency_unavailable", "detail": str(exc)},
            status_code=503,
        )

    app.add_exception_handler(RuntimeIoBusy, runtime_io_unavailable)
    app.add_exception_handler(RuntimeIoTimeout, runtime_io_unavailable)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        schedule_cleanup()
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; "
            "script-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        if settings.environment == "production" and settings.transport_mode == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        artifact_call = asyncio.create_task(
            runtime_io.call(_artifact_info, settings, timeout_seconds=2.0)
        )
        database_call = asyncio.create_task(
            runtime_io.call(database.ping, timeout_seconds=2.0)
        )
        try:
            apk_path, apk_hash = await artifact_call
        except (RuntimeIoBusy, RuntimeIoTimeout, OSError):
            apk_path, apk_hash = None, None
        try:
            database_ok = await database_call
        except (RuntimeIoBusy, RuntimeIoTimeout, OSError, RuntimeError):
            database_ok = False
        return JSONResponse(
            {
                "ok": database_ok,
                "service": "oin-v01",
                "version": __version__,
                "public_base_url": settings.public_base_url,
                "transport_mode": settings.transport_mode,
                "database": "ok" if database_ok else "error",
                "apk_available": apk_path is not None,
                "apk_version": settings.apk_version if apk_path else None,
                "apk_sha256": apk_hash,
            }
        )

    @app.get("/")
    async def root(request: Request) -> RedirectResponse:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        target = "/devices" if session else "/login"
        return _redirect(settings, target)

    @app.get("/register")
    async def register_page(request: Request) -> Response:
        if await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        ):
            return _redirect(settings, "/devices")
        preauth, csrf = await runtime_io.call(store.create_preauth_session)
        response = HTMLResponse(auth_form(settings, mode="register", csrf=csrf))
        _set_cookie(response, PREAUTH_COOKIE, preauth, settings, settings.preauth_ttl_seconds)
        return response

    @app.post("/register")
    async def register_submit(request: Request) -> Response:
        try:
            form = await _form_body(request)
        except SecurityError:
            return _json_error(400, "invalid_request", "Invalid registration request")
        preauth = request.cookies.get(PREAUTH_COOKIE)
        if not await runtime_io.call(
            store.validate_preauth_session, preauth, form.get("csrf")
        ):
            return _json_error(403, "csrf_failed", "Registration form expired; reload and try again")
        username = form.get("username", "")
        email = form.get("email", "")
        try:
            account = await runtime_io.call(
                store.create_account, username, email, form.get("password", "")
            )
        except (SecurityError, ConflictError) as exc:
            message = "用户名、邮箱或密码不符合要求，或账号已存在。"
            return HTMLResponse(
                auth_form(
                    settings,
                    mode="register",
                    csrf=form.get("csrf", ""),
                    error=message,
                    username=username,
                    email=email,
                ),
                status_code=400 if isinstance(exc, SecurityError) else 409,
            )
        await runtime_io.call(store.delete_preauth_session, preauth)
        token, _, _ = await runtime_io.call(store.create_web_session, account["id"])
        response = _redirect(settings, "/devices")
        _delete_cookie(response, PREAUTH_COOKIE, settings)
        _set_cookie(response, WEB_COOKIE, token, settings, settings.web_session_ttl_seconds)
        return response

    @app.get("/login")
    async def login_page(request: Request) -> Response:
        if await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        ):
            return _redirect(settings, "/devices")
        preauth, csrf = await runtime_io.call(store.create_preauth_session)
        response = HTMLResponse(auth_form(settings, mode="login", csrf=csrf))
        _set_cookie(response, PREAUTH_COOKIE, preauth, settings, settings.preauth_ttl_seconds)
        return response

    @app.post("/login")
    async def login_submit(request: Request) -> Response:
        try:
            form = await _form_body(request)
        except SecurityError:
            return _json_error(400, "invalid_request", "Invalid login request")
        preauth = request.cookies.get(PREAUTH_COOKIE)
        if not await runtime_io.call(
            store.validate_preauth_session, preauth, form.get("csrf")
        ):
            return _json_error(403, "csrf_failed", "Login form expired; reload and try again")
        identifier = form.get("identifier", "")
        remote_hint = request.client.host if request.client else "unknown"
        limit_key = limiter.key(remote_hint, identifier)
        if limiter.blocked(limit_key):
            return HTMLResponse(
                auth_form(settings, mode="login", csrf=form.get("csrf", ""), error="尝试次数过多，请稍后再试。", username=identifier),
                status_code=429,
            )
        account = await runtime_io.call(
            store.authenticate, identifier, form.get("password", "")
        )
        if account is None:
            limiter.fail(limit_key)
            return HTMLResponse(
                auth_form(settings, mode="login", csrf=form.get("csrf", ""), error="用户名/邮箱或密码错误。", username=identifier),
                status_code=401,
            )
        limiter.succeed(limit_key)
        await runtime_io.call(store.delete_preauth_session, preauth)
        token, _, _ = await runtime_io.call(store.create_web_session, account["id"])
        response = _redirect(settings, "/devices")
        _delete_cookie(response, PREAUTH_COOKIE, settings)
        _set_cookie(response, WEB_COOKIE, token, settings, settings.web_session_ttl_seconds)
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        try:
            form = await _form_body(request)
        except SecurityError:
            form = {}
        if session is None or not _csrf_valid(request, AuthContext(session.account, session, None), form.get("csrf")):
            return _json_error(403, "csrf_failed", "Invalid logout request")
        await runtime_io.call(
            store.delete_web_session, request.cookies.get(WEB_COOKIE)
        )
        response = _redirect(settings, "/login")
        _delete_cookie(response, WEB_COOKIE, settings)
        return response

    @app.get("/devices")
    async def list_devices_page(request: Request) -> Response:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        if session is None:
            return _redirect(settings, "/login")
        devices = await runtime_io.call(store.list_devices, session.account["id"])
        return HTMLResponse(
            devices_page(
                settings,
                session.account["username"],
                session.csrf_token,
                await _effective_realtime_devices(devices, hub),
            )
        )

    @app.get("/devices/{device_id}")
    async def show_device_page(request: Request, device_id: str) -> Response:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        if session is None:
            return _redirect(settings, "/login")
        device = await runtime_io.call(
            store.get_device, session.account["id"], device_id
        )
        if device is None:
            return _json_error(404, "not_found", "Device not found")
        realtime_sessions = await hub.realtime_sessions([device_id])
        device = _effective_realtime_device(
            device, realtime_sessions.get(device_id)
        )
        owner_devices = await runtime_io.call(
            store.list_devices, session.account["id"]
        )
        return HTMLResponse(
            device_page(
                settings,
                session.account["username"],
                session.csrf_token,
                device,
                await runtime_io.call(
                    store.list_recent_tasks, session.account["id"], device_id
                ),
                await _effective_realtime_devices(owner_devices, hub),
                await runtime_io.call(
                    store.list_device_credential_repair_requests,
                    session.account["id"],
                    device_id,
                ),
                await runtime_io.call(
                    store.list_outbox_reconciliations,
                    session.account["id"],
                    device_id,
                ),
            )
        )

    @app.post("/devices/{device_id}/tasks")
    async def create_task_page(request: Request, device_id: str) -> Response:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        if session is None:
            return _redirect(settings, "/login")
        try:
            form = await _form_body(request)
            context = AuthContext(session.account, session, None)
            if not _csrf_valid(request, context, form.get("csrf")):
                return _json_error(403, "csrf_failed", "Invalid task request")
            raw_task_type = form.get("task_type")
            raw_parameters: dict[str, Any] = {}
            if raw_task_type == "action":
                raw_parameters = {"action": form.get("action", "")}
                if raw_parameters["action"] == "launch_app":
                    raw_parameters["package"] = form.get("package", "")
            task_type, parameters, ttl = validate_task_request(
                raw_task_type, raw_parameters, DEFAULT_TASK_TTL_SECONDS
            )
            task, _ = await runtime_io.call(
                store.create_task,
                session.account["id"],
                device_id,
                task_type,
                new_opaque_token(18),
                parameters,
                ttl,
            )
        except SecurityError:
            return _json_error(422, "invalid_task", "Unknown or invalid task")
        except NotFoundError:
            return _json_error(404, "not_found", "Device not found")
        except TaskChannelNotReadyError:
            return _json_error(
                409,
                "device_task_channel_not_ready",
                "Device is online for diagnostics but its durable task channel is not ready",
            )
        except TaskCapacityError:
            return _json_error(
                409,
                "device_task_capacity_reached",
                "Device already has the maximum number of active tasks",
            )
        except UnsupportedDeviceTaskError:
            return _json_error(
                409,
                "device_task_type_unsupported",
                "The installed device agent does not advertise this task type",
            )
        await _dispatch_if_fresh(
            store, hub, runtime_io, task, settings.dispatch_freshness_seconds
        )
        return _redirect(settings, f"/devices/{device_id}")

    @app.post("/devices/{device_id}/revoke")
    async def revoke_device_page(request: Request, device_id: str) -> Response:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        if session is None:
            return _redirect(settings, "/login")
        try:
            form = await _form_body(request)
        except SecurityError:
            return _json_error(400, "invalid_request", "Invalid revoke request")
        if not _csrf_valid(request, AuthContext(session.account, session, None), form.get("csrf")):
            return _json_error(403, "csrf_failed", "Invalid revoke request")
        if not await runtime_io.call(
            store.revoke_device, session.account["id"], device_id
        ):
            return _json_error(404, "not_found", "Device not found")
        await hub.disconnect(device_id, code=4403, reason="device_revoked")
        return _redirect(settings, f"/devices/{device_id}")

    @app.post(
        "/devices/{device_id}/credential-repair-requests/{request_id}/approve"
    )
    async def approve_device_credential_repair_page(
        request: Request, device_id: str, request_id: str
    ) -> Response:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        if session is None:
            return _redirect(settings, "/login")
        try:
            form = await _form_body(request)
        except SecurityError:
            return _json_error(400, "invalid_request", "Invalid approval request")
        if not _csrf_valid(
            request, AuthContext(session.account, session, None), form.get("csrf")
        ):
            return _json_error(403, "csrf_failed", "Invalid approval request")
        try:
            await runtime_io.call(
                store.authorize_device_credential_repair,
                session.account["id"],
                device_id,
                request_id,
            )
        except NotFoundError:
            return _json_error(404, "not_found", "Credential repair request not found")
        except ConflictError as exc:
            return _json_error(
                409,
                str(exc) or "credential_repair_conflict",
                "Credential repair request is not approvable",
            )
        return _redirect(settings, f"/devices/{device_id}")

    @app.post("/devices/{device_id}/outbox-reconciliations")
    async def create_outbox_reconciliation_page(
        request: Request, device_id: str
    ) -> Response:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        if session is None:
            return _redirect(settings, "/login")
        try:
            form = await _form_body(request)
        except SecurityError:
            return _json_error(400, "invalid_request", "Invalid reconciliation request")
        if not _csrf_valid(
            request, AuthContext(session.account, session, None), form.get("csrf")
        ):
            return _json_error(403, "csrf_failed", "Invalid reconciliation request")
        try:
            old_device_id = safe_text(
                form.get("old_device_id"),
                maximum=80,
                field="old_device_id",
                allow_empty=False,
            )
            await runtime_io.call(
                store.create_outbox_reconciliation_authorization,
                session.account["id"],
                old_device_id,
                device_id,
                new_opaque_token(18),
            )
        except SecurityError:
            return _json_error(422, "invalid_old_scope", "Exact old scope is required")
        except NotFoundError:
            return _json_error(404, "not_found", "Device scope pair not found")
        except ConflictError as exc:
            return _json_error(
                409,
                str(exc) or "reconciliation_conflict",
                "Outbox reconciliation authorization is not permitted",
            )
        return _redirect(settings, f"/devices/{device_id}")

    @app.get("/download")
    async def show_download(request: Request) -> HTMLResponse:
        session = await runtime_io.call(
            store.get_web_session, request.cookies.get(WEB_COOKIE)
        )
        path, digest = await runtime_io.call(_artifact_info, settings)
        return HTMLResponse(
            download_page(
                settings,
                apk_available=path is not None,
                apk_sha256=digest,
                username=session.account["username"] if session else None,
                csrf=session.csrf_token if session else None,
            )
        )

    @app.get("/api/v1/app-update")
    async def app_update() -> Response:
        from .app_updates import release_info
        try:
            value = await runtime_io.call(release_info, settings)
        except FileNotFoundError:
            return _json_error(404, "update_not_published", "Update is not published")
        except (OSError, ValueError, TypeError, KeyError):
            return _json_error(503, "update_metadata_invalid", "Update metadata is unavailable")
        return JSONResponse(value, headers={"Cache-Control":"no-store"})

    @app.get("/download/apk")
    async def download_apk(sha256: str | None = None) -> Response:
        path, digest = await runtime_io.call(_artifact_info, settings)
        if path is None:
            return _json_error(404, "apk_not_published", "APK is not published")
        if sha256 is not None and sha256 != digest:
            return _json_error(409, "update_release_changed", "Please check for updates again")
        safe_version = "".join(character for character in settings.apk_version if character.isalnum() or character in ".-_")
        return FileResponse(
            path,
            media_type="application/vnd.android.package-archive",
            filename=f"oin-android-agent-{safe_version or 'v01'}.apk",
            headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/download/qr.svg")
    async def download_qr() -> Response:
        path, _ = await runtime_io.call(_artifact_info, settings)
        if path is None:
            return _json_error(404, "apk_not_published", "APK is not published")
        image = qrcode.make(
            settings.public_url("/download/apk"),
            image_factory=qrcode.image.svg.SvgPathImage,
            border=2,
        )
        stream = io.BytesIO()
        image.save(stream)
        return Response(stream.getvalue(), media_type="image/svg+xml")

    @app.post("/api/v1/accounts")
    async def api_register(request: Request) -> Response:
        try:
            body = await _json_body(request)
            if set(body) - {"username", "email", "password"}:
                raise SecurityError("unknown_account_fields")
            account = await runtime_io.call(
                store.create_account,
                body.get("username", ""),
                body.get("email"),
                body.get("password", ""),
            )
        except SecurityError:
            return _json_error(422, "invalid_account", "Username, email, or password is invalid")
        except ConflictError:
            return _json_error(409, "account_exists", "Username or email is already registered")
        return JSONResponse({"account": account}, status_code=201)

    @app.post("/api/v1/operations/workspace-bootstrap")
    async def api_b4_workspace_bootstrap(request: Request) -> Response:
        return await b4_write(request, identity_store.bootstrap_workspace)

    @app.get("/api/v1/operations/authorized-context")
    async def api_b4_authorized_context(request: Request) -> Response:
        context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "mobile_auth_required", "Bearer authentication is required"
            )
        if request.query_params:
            return _json_error(
                400,
                "validation_error",
                "Authorized context does not accept resource selectors",
            )
        try:
            result = await runtime_io.call(
                identity_store.authorized_context,
                context.account["id"],
                context.account["user_id"],
            )
        except (
            SecurityError,
            OperationsNotFound,
            OperationsConflict,
            OperationsAuthentication,
        ) as exc:
            return _operations_error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/operations/identity-evidence/pending")
    async def api_b4_pending_identity_evidence(request: Request) -> Response:
        context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "mobile_auth_required", "Bearer authentication is required"
            )
        if request.query_params:
            return _k2_error_response(
                SecurityError("pending_identity_evidence_query_forbidden")
            )
        try:
            result = await runtime_io.call(
                identity_store.list_pending_identity_evidence,
                context.account["id"],
                context.account["user_id"],
            )
        except (
            SecurityError,
            OperationsNotFound,
            OperationsConflict,
            OperationsAuthentication,
            OperationsForbidden,
        ) as exc:
            return _k2_error_response(exc)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/operations/identity-confirmations")
    async def api_b4_confirm_identity(request: Request) -> Response:
        context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "mobile_auth_required", "Bearer authentication is required"
            )
        if request.query_params:
            return _k2_error_response(
                SecurityError("identity_confirmation_query_forbidden")
            )
        try:
            body = await _json_body(request, maximum=16 * 1024)
            outcome = await runtime_io.call(
                identity_store.confirm_identity,
                context.account["id"],
                context.account["user_id"],
                body,
            )
        except (
            SecurityError,
            OperationsNotFound,
            OperationsConflict,
            OperationsAuthentication,
            OperationsForbidden,
        ) as exc:
            return _k2_error_response(exc)
        payload = {
            "disposition": (
                "idempotent_replay" if outcome.replayed else "applied"
            ),
            **outcome.value,
        }
        confirmation_result = payload["confirmation"]["result"]
        if outcome.replayed and confirmation_result == "CONFIRMED":
            payload["confirmation"]["result"] = "IDEMPOTENT_REPLAY"
            confirmation_result = "IDEMPOTENT_REPLAY"
        status = 409 if confirmation_result == "REJECTED" else (
            200 if outcome.replayed else 201
        )
        return JSONResponse(
            payload,
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/v1/operations/shops")
    async def api_b4_create_shop(request: Request) -> Response:
        return await b4_write(request, identity_store.create_shop)

    @app.get("/api/v1/operations/shops/{shop_id}")
    async def api_b4_get_shop(request: Request, shop_id: str) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None or context.web_session is None:
            return _json_error(401, "authentication_required", "Web controller login is required")
        try:
            tenant_id = validate_route_identifier(request.query_params.get("tenant_id"), "tenant_id")
            merchant_id = validate_route_identifier(request.query_params.get("merchant_id"), "merchant_id")
            shop_id = validate_route_identifier(shop_id, "shop_id")
            result = await runtime_io.call(
                identity_store.get_shop,
                context.account["id"],
                context.account["user_id"],
                tenant_id,
                merchant_id,
                shop_id,
            )
        except (SecurityError, OperationsNotFound, OperationsConflict, OperationsAuthentication) as exc:
            return _operations_error_response(exc)
        return JSONResponse(result)

    @app.post("/api/v1/operations/principals")
    async def api_b4_create_principal(request: Request) -> Response:
        return await b4_write(request, identity_store.create_principal)

    @app.get("/api/v1/operations/principals/{principal_id}")
    async def api_b4_get_principal(request: Request, principal_id: str) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None or context.web_session is None:
            return _json_error(401, "authentication_required", "Web controller login is required")
        try:
            tenant_id = validate_route_identifier(request.query_params.get("tenant_id"), "tenant_id")
            merchant_id = validate_route_identifier(request.query_params.get("merchant_id"), "merchant_id")
            principal_id = validate_route_identifier(principal_id, "principal_id")
            result = await runtime_io.call(
                identity_store.get_principal,
                context.account["id"],
                context.account["user_id"],
                tenant_id,
                merchant_id,
                principal_id,
            )
        except (SecurityError, OperationsNotFound, OperationsConflict, OperationsAuthentication) as exc:
            return _operations_error_response(exc)
        return JSONResponse(result)

    @app.post("/api/v1/operations/enrollment-authorizations")
    async def api_b4_authorize_enrollment(request: Request) -> Response:
        return await b4_write(request, identity_store.authorize_enrollment)

    @app.post("/api/v2/runtime/registration-challenges")
    async def api_b4_registration_challenge(request: Request) -> Response:
        return await b4_runtime_command(request, identity_store.create_registration_challenge)

    @app.post("/api/v2/runtime/connectors")
    async def api_b4_register_connector(request: Request) -> Response:
        return await b4_runtime_command(request, identity_store.register_connector)

    @app.post("/api/v2/runtime/identity-observations")
    async def api_b4_record_identity_observation(request: Request) -> Response:
        if request.query_params:
            return _k2_error_response(
                SecurityError("identity_ingress_query_forbidden"),
                canonical=True,
            )
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return _k2_error_response(
                SecurityError("identity_ingress_content_type_invalid"),
                canonical=True,
            )
        if any(
            request.headers.get(name)
            for name in ("authorization", "cookie", "x-csrf-token")
        ):
            return _k2_error_response(
                SecurityError("identity_ingress_credential_header_forbidden"),
                canonical=True,
            )
        try:
            body = await _jcs_json_body(
                request,
                maximum=PDD_IDENTITY_INGRESS_MAX_BYTES,
            )
            outcome = await runtime_io.call(
                identity_store.record_authenticated_identity_observation,
                body,
            )
        except (
            SecurityError,
            OperationsNotFound,
            OperationsConflict,
            OperationsAuthentication,
            OperationsForbidden,
        ) as exc:
            return _k2_error_response(exc, canonical=True)
        payload = {
            "disposition": (
                "idempotent_replay" if outcome.replayed else "applied"
            ),
            **outcome.value,
        }
        return Response(
            content=canonical_jcs_bytes(payload),
            media_type="application/json",
            status_code=200 if outcome.replayed else 201,
        )

    @app.post("/api/v1/operations/profiles")
    async def api_b4_create_profile(request: Request) -> Response:
        return await b4_write(request, identity_store.create_profile)

    @app.post("/api/v1/operations/bindings")
    async def api_b4_create_binding(request: Request) -> Response:
        return await b4_write(request, identity_store.create_binding)

    @app.post("/api/v1/operations/route-activations")
    async def api_b4_issue_route_activation(request: Request) -> Response:
        return await b4_write(request, identity_store.issue_route_activation)

    @app.post("/api/v2/runtime/route-activations/consume")
    async def api_b4_consume_route_activation(request: Request) -> Response:
        return await b4_runtime_command(request, identity_store.consume_route_activation)

    @app.get("/api/v1/operations/routes")
    async def api_b4_list_routes(request: Request) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None or context.web_session is None:
            return _json_error(401, "authentication_required", "Web controller login is required")
        try:
            tenant_id = validate_route_identifier(request.query_params.get("tenant_id"), "tenant_id")
            result = await runtime_io.call(
                identity_store.list_routes,
                context.account["id"],
                context.account["user_id"],
                tenant_id,
            )
        except (SecurityError, OperationsNotFound, OperationsConflict, OperationsAuthentication) as exc:
            return _operations_error_response(exc)
        return JSONResponse(result)

    @app.post("/api/v1/mobile/sessions")
    async def api_mobile_login(request: Request) -> Response:
        try:
            body = await _json_body(request, maximum=16 * 1024)
            if set(body) - {"identifier", "password"}:
                raise SecurityError("unknown_login_fields")
            identifier = safe_text(body.get("identifier", ""), maximum=254, field="identifier", allow_empty=False)
            password = body.get("password", "")
            if not isinstance(password, str) or len(password) > 128:
                raise SecurityError("invalid_password")
        except SecurityError:
            return _json_error(422, "invalid_login", "Invalid login request")
        remote_hint = request.client.host if request.client else "unknown"
        limit_key = limiter.key(remote_hint, identifier)
        if limiter.blocked(limit_key):
            return _json_error(429, "rate_limited", "Too many login attempts")
        account = await runtime_io.call(store.authenticate, identifier, password)
        if account is None:
            limiter.fail(limit_key)
            return _json_error(401, "invalid_credentials", "Identifier or password is incorrect")
        limiter.succeed(limit_key)
        token, expires_at = await runtime_io.call(
            store.create_mobile_session, account["id"]
        )
        return JSONResponse(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_at": expires_at,
                "account": account,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/api/v1/mobile/sessions/current")
    async def api_mobile_logout(request: Request) -> Response:
        token = _bearer(request)
        if not token or not await runtime_io.call(store.get_mobile_account, token):
            return _json_error(401, "unauthorized", "Authentication required")
        await runtime_io.call(store.revoke_mobile_session, token)
        return Response(status_code=204)

    @app.get("/api/v1/accounts/me")
    async def api_me(request: Request) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(401, "unauthorized", "Authentication required")
        return JSONResponse({"account": context.account})

    @app.post("/api/v1/device-enrollment-challenges")
    async def api_enrollment_challenge(request: Request) -> Response:
        context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(401, "mobile_auth_required", "Mobile authentication required")
        try:
            body = await _json_body(request, maximum=16 * 1024)
            if set(body) != {"public_key"}:
                raise SecurityError("invalid_enrollment_challenge_fields")
            public_key = load_p256_public_key(body["public_key"])
            challenge = await runtime_io.call(
                store.create_enrollment_challenge, context.account["id"], public_key
            )
        except (SecurityError, KeyError):
            return _json_error(422, "invalid_public_key", "A P-256 public key is required")
        return JSONResponse(
            {
                "challenge_id": challenge.id,
                "nonce": challenge.nonce,
                "expires_at": challenge.expires_at,
                "algorithm": "ECDSA_P256_SHA256_DER",
                "public_key_encoding": "SPKI_DER",
                "signature_encoding": "ASN1_DER",
            },
            status_code=201,
        )

    @app.post("/api/v1/devices")
    async def api_enroll_device(request: Request) -> Response:
        context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(401, "mobile_auth_required", "Mobile authentication required")
        try:
            body = await _json_body(request)
            if set(body) != {"challenge_id", "payload", "signature"}:
                raise SecurityError("invalid_device_registration_fields")
            challenge_id = safe_text(
                body["challenge_id"], maximum=80, field="challenge_id", allow_empty=False
            )
            signature = safe_text(body["signature"], maximum=512, field="signature", allow_empty=False)
            payload = validate_device_payload(body["payload"])
            device, repeated = await runtime_io.call(
                store.enroll_device,
                context.account["id"],
                challenge_id,
                payload,
                signature,
            )
        except SecurityError:
            return _json_error(422, "invalid_device_payload", "Device registration payload is invalid")
        except AuthenticationError as exc:
            await runtime_io.call(
                store.record_audit,
                context.account["id"],
                None,
                "device_enrollment_rejected",
                False,
                {"reason": str(exc)[:80]},
            )
            return _json_error(401, "invalid_device_signature", "Enrollment challenge or signature is invalid")
        except ConflictError:
            return _json_error(409, "device_key_conflict", "Device key is already bound or revoked")
        return JSONResponse(
            {"device": device, "registration": "existing" if repeated else "created"},
            status_code=200 if repeated else 201,
        )

    @app.post("/api/v1/devices/{device_id}/credential-repair-requests")
    async def api_request_device_credential_repair(
        request: Request, device_id: str
    ) -> Response:
        context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "mobile_auth_required", "Mobile authentication required"
            )
        try:
            body = await _json_body(request, maximum=16 * 1024)
            if set(body) != {"public_key"}:
                raise SecurityError("invalid_credential_repair_request_fields")
            public_key = load_p256_public_key(body["public_key"])
            repair_request, created = await runtime_io.call(
                store.create_device_credential_repair_request,
                context.account["id"],
                device_id,
                public_key,
            )
        except (SecurityError, KeyError):
            return _json_error(422, "invalid_public_key", "A P-256 public key is required")
        except NotFoundError:
            return _json_error(404, "not_found", "Device not found")
        except ConflictError as exc:
            code = str(exc) or "credential_repair_conflict"
            return _json_error(409, code, "Credential repair request is not permitted")
        return JSONResponse(
            {"repair_request": repair_request, "created": created},
            status_code=201 if created else 200,
        )

    @app.get(
        "/api/v1/devices/{device_id}/credential-repair-requests/{request_id}"
    )
    async def api_get_device_credential_repair_request(
        request: Request, device_id: str, request_id: str
    ) -> Response:
        context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "mobile_auth_required", "Mobile authentication required"
            )
        repair_request = await runtime_io.call(
            store.get_device_credential_repair_request,
            context.account["id"],
            device_id,
            request_id,
        )
        if repair_request is None:
            return _json_error(404, "not_found", "Credential repair request not found")
        return JSONResponse({"repair_request": repair_request})

    @app.post(
        "/api/v1/devices/{device_id}/credential-repair-requests/{request_id}/approve"
    )
    async def api_approve_device_credential_repair(
        request: Request, device_id: str, request_id: str
    ) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "controller_auth_required", "Controller authentication required"
            )
        if not _csrf_valid(request, context):
            return _json_error(403, "csrf_failed", "CSRF token required")
        try:
            repair_request, approved = await runtime_io.call(
                store.authorize_device_credential_repair,
                context.account["id"],
                device_id,
                request_id,
            )
        except NotFoundError:
            return _json_error(404, "not_found", "Credential repair request not found")
        except ConflictError as exc:
            return _json_error(
                409,
                str(exc) or "credential_repair_conflict",
                "Credential repair request is not approvable",
            )
        return JSONResponse(
            {"repair_request": repair_request, "approved": approved},
            status_code=201 if approved else 200,
        )

    @app.post("/api/v1/devices/{device_id}/credential-repair")
    async def api_repair_device_credential(request: Request, device_id: str) -> Response:
        context = await _mobile_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "mobile_auth_required", "Mobile authentication required"
            )
        try:
            body = await _json_body(request)
            if set(body) != {"challenge_id", "payload", "signature"}:
                raise SecurityError("invalid_credential_repair_fields")
            challenge_id = safe_text(
                body["challenge_id"],
                maximum=80,
                field="challenge_id",
                allow_empty=False,
            )
            signature = safe_text(
                body["signature"], maximum=512, field="signature", allow_empty=False
            )
            payload = validate_device_payload(body["payload"])
            device, repeated = await runtime_io.call(
                store.repair_device_credential,
                context.account["id"],
                device_id,
                challenge_id,
                payload,
                signature,
            )
        except (SecurityError, KeyError):
            return _json_error(
                422, "invalid_credential_repair", "Credential repair payload is invalid"
            )
        except AuthenticationError:
            return _json_error(
                401,
                "credential_repair_not_authorized",
                "Credential repair challenge or proof is invalid",
            )
        except ConflictError as exc:
            return _json_error(
                409,
                str(exc) or "credential_repair_conflict",
                "Credential repair conflicts with an existing device credential",
            )
        return JSONResponse(
            {"device": device, "repair": "existing" if repeated else "rotated"}
        )

    @app.post("/api/v1/device-revocation-challenges")
    async def api_device_revocation_challenge(request: Request) -> Response:
        return _json_error(
            410,
            "mobile_self_revoke_disabled",
            "Device revocation is available only in the authenticated web controller",
        )

    @app.post("/api/v1/devices/{device_id}/self-revoke")
    async def api_self_revoke_device(request: Request, device_id: str) -> Response:
        return _json_error(
            410,
            "mobile_self_revoke_disabled",
            "Device revocation is available only in the authenticated web controller",
        )

    @app.get("/api/v1/devices")
    async def api_list_devices(request: Request) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(401, "controller_auth_required", "Controller authentication required")
        devices = await runtime_io.call(store.list_devices, context.account["id"])
        return JSONResponse(
            {"devices": await _effective_realtime_devices(devices, hub)}
        )

    @app.get("/api/v1/devices/{device_id}")
    async def api_get_device(request: Request, device_id: str) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(401, "controller_auth_required", "Controller authentication required")
        device = await runtime_io.call(
            store.get_device, context.account["id"], device_id
        )
        if device is None:
            return _json_error(404, "not_found", "Device not found")
        realtime_sessions = await hub.realtime_sessions([device_id])
        return JSONResponse(
            {
                "device": _effective_realtime_device(
                    device, realtime_sessions.get(device_id)
                )
            }
        )

    @app.post("/api/v1/devices/{device_id}/revoke")
    async def api_revoke_device(request: Request, device_id: str) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(401, "controller_auth_required", "Controller authentication required")
        if not _csrf_valid(request, context):
            return _json_error(403, "csrf_failed", "CSRF token required")
        if not await runtime_io.call(
            store.revoke_device, context.account["id"], device_id
        ):
            return _json_error(404, "not_found", "Device not found")
        await hub.disconnect(device_id, code=4403, reason="device_revoked")
        return JSONResponse({"revoked": True, "device_id": device_id})

    @app.post("/api/v1/devices/{device_id}/outbox-reconciliations")
    async def api_create_outbox_reconciliation(
        request: Request, device_id: str
    ) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "controller_auth_required", "Controller authentication required"
            )
        if not _csrf_valid(request, context):
            return _json_error(403, "csrf_failed", "CSRF token required")
        try:
            body = await _json_body(request, maximum=16 * 1024)
            if set(body) - {"old_device_id", "idempotency_key"}:
                raise SecurityError("invalid_reconciliation_authorization_fields")
            old_device_id = safe_text(
                body.get("old_device_id"),
                maximum=80,
                field="old_device_id",
                allow_empty=False,
            )
            idempotency_key = request.headers.get("idempotency-key") or body.get(
                "idempotency_key"
            )
            idempotency_key = safe_text(
                idempotency_key,
                maximum=128,
                field="idempotency_key",
                allow_empty=False,
            )
            if len(idempotency_key) < 8:
                raise SecurityError("idempotency_key_too_short")
            authorization, created = await runtime_io.call(
                store.create_outbox_reconciliation_authorization,
                context.account["id"],
                old_device_id,
                device_id,
                idempotency_key,
            )
        except SecurityError:
            return _json_error(
                422,
                "invalid_reconciliation_authorization",
                "Exact old scope, new scope, and idempotency key are required",
            )
        except NotFoundError:
            return _json_error(404, "not_found", "Device scope pair not found")
        except ConflictError as exc:
            return _json_error(
                409,
                str(exc) or "reconciliation_conflict",
                "Outbox reconciliation authorization is not permitted",
            )
        return JSONResponse(
            {"authorization": authorization, "created": created},
            status_code=201 if created else 200,
        )

    @app.get("/api/v1/devices/{device_id}/outbox-reconciliations")
    async def api_list_outbox_reconciliations(
        request: Request, device_id: str
    ) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(
                401, "controller_auth_required", "Controller authentication required"
            )
        device = await runtime_io.call(
            store.get_device, context.account["id"], device_id
        )
        if device is None:
            return _json_error(404, "not_found", "Device not found")
        return JSONResponse(
            {
                "authorizations": await runtime_io.call(
                    store.list_outbox_reconciliations,
                    context.account["id"],
                    device_id,
                )
            }
        )

    @app.post("/api/v1/devices/{device_id}/tasks")
    async def api_create_task(request: Request, device_id: str) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(401, "controller_auth_required", "Controller authentication required")
        if not _csrf_valid(request, context):
            return _json_error(403, "csrf_failed", "CSRF token required")
        try:
            body = await _json_body(request, maximum=512 * 1024)
            if set(body) - {"task_type", "parameters", "ttl_seconds", "idempotency_key"}:
                raise SecurityError("unknown_task_fields")
            task_type, parameters, ttl = validate_task_request(
                body.get("task_type"),
                body.get("parameters", {}),
                body.get("ttl_seconds", DEFAULT_TASK_TTL_SECONDS),
            )
            idempotency_key = request.headers.get("idempotency-key") or body.get("idempotency_key")
            idempotency_key = safe_text(
                idempotency_key, maximum=128, field="idempotency_key", allow_empty=False
            )
            if len(idempotency_key) < 8:
                raise SecurityError("idempotency_key_too_short")
            task, created = await runtime_io.call(
                store.create_task,
                context.account["id"],
                device_id,
                task_type,
                idempotency_key,
                parameters,
                ttl,
            )
        except SecurityError:
            return _json_error(422, "invalid_task", "Task type, parameters, TTL, or idempotency key is invalid")
        except NotFoundError:
            return _json_error(404, "not_found", "Device not found")
        except TaskChannelNotReadyError:
            return _json_error(
                409,
                "device_task_channel_not_ready",
                "Device is online for diagnostics but its durable task channel is not ready",
            )
        except TaskCapacityError:
            return _json_error(
                409,
                "device_task_capacity_reached",
                "Device already has the maximum number of active tasks",
            )
        except UnsupportedDeviceTaskError:
            return _json_error(
                409,
                "device_task_type_unsupported",
                "The installed device agent does not advertise this task type",
            )
        except ConflictError:
            return _json_error(
                409,
                "idempotency_conflict",
                "Idempotency key was already used for a different task request",
            )
        dispatch_outcome = TaskDispatchOutcome.EXISTING_NOT_RECHECKED
        if created:
            dispatch_outcome = await _dispatch_if_fresh(
                store, hub, runtime_io, task, settings.dispatch_freshness_seconds
            )
            refreshed = await runtime_io.call(
                store.get_task, context.account["id"], device_id, task["id"]
            )
            if refreshed:
                task = refreshed
        return JSONResponse(
            {
                "task": task,
                "created": created,
                "delivery": _task_delivery_contract(task, dispatch_outcome),
            },
            status_code=201 if created else 200,
        )

    @app.get("/api/v1/devices/{device_id}/tasks/{task_id}")
    async def api_get_task(request: Request, device_id: str, task_id: str) -> Response:
        context = await _web_context(request, store, runtime_io)
        if context is None:
            return _json_error(401, "controller_auth_required", "Controller authentication required")
        task = await runtime_io.call(
            store.get_task, context.account["id"], device_id, task_id
        )
        if task is None:
            return _json_error(404, "not_found", "Task not found")
        return JSONResponse({"task": task})

    @app.websocket("/api/v1/device-channel")
    async def device_channel(websocket: WebSocket) -> None:
        schedule_cleanup()
        await websocket.accept()
        challenge_id = str(uuid.uuid4())
        nonce = new_opaque_token(32)
        issued_at = now_seconds()
        connection: DeviceConnection | None = None
        session_id: str | None = None
        disconnect_reason = "connection_closed"
        try:
            await websocket.send_json(
                {
                    "type": "auth_challenge",
                    "protocol_version": PROTOCOL_VERSION,
                    "challenge_id": challenge_id,
                    "nonce": nonce,
                    "issued_at": issued_at,
                    "algorithm": "ECDSA_P256_SHA256_DER",
                    "signature_encoding": "ASN1_DER",
                }
            )
            raw = await asyncio.wait_for(
                websocket.receive_text(), timeout=settings.websocket_auth_timeout_seconds
            )
            if len(raw.encode("utf-8")) > 256 * 1024:
                raise SecurityError("auth_message_too_large")
            auth = _strict_json_loads(raw)
            if not isinstance(auth, dict) or auth.get("type") != "auth":
                raise SecurityError("auth_message_required")
            legacy_auth_fields = {
                "type",
                "protocol_version",
                "challenge_id",
                "device_id",
                "signature",
                "payload",
            }
            transport_auth_fields = {
                "type",
                "protocol_version",
                "challenge_id",
                "device_id",
                "signature",
                "transport_auth",
            }
            auth_fields = set(auth)
            if auth_fields != legacy_auth_fields and auth_fields != transport_auth_fields:
                raise SecurityError("invalid_auth_message_fields")
            if auth.get("protocol_version") != PROTOCOL_VERSION:
                raise SecurityError("unsupported_protocol_version")
            if auth.get("challenge_id") != challenge_id:
                raise SecurityError("challenge_mismatch")
            device_id = safe_text(auth.get("device_id"), maximum=80, field="device_id", allow_empty=False)
            signature = safe_text(auth.get("signature"), maximum=512, field="signature", allow_empty=False)
            payload: dict[str, Any] | None = None
            transport_auth: dict[str, Any] | None = None
            if auth_fields == legacy_auth_fields:
                payload = validate_device_payload(auth.get("payload"))
                if payload["app_version_code"] >= 26:
                    raise SecurityError("code26_requires_transport_auth")
                signed_auth_message = websocket_auth_message(
                    device_id, challenge_id, nonce, issued_at, payload
                )
                negotiated_task_store_protocol = task_store_protocol(
                    payload["capabilities"]
                )
                code26_independent_lanes = False
                advertised_task_channel_ready = payload_task_channel_ready(
                    payload["capabilities"]
                )
            else:
                transport_auth = validate_transport_auth(auth.get("transport_auth"))
                signed_auth_message = websocket_transport_auth_message(
                    device_id, challenge_id, nonce, issued_at, transport_auth
                )
                negotiated_task_store_protocol = transport_auth["task_store_protocol"]
                code26_independent_lanes = True
                advertised_task_channel_ready = False
            credential = await runtime_io.call(store.get_device_credential, device_id)
            if credential is None:
                if await runtime_io.call(store.get_device_auth_state, device_id) == "revoked":
                    disconnect_reason = "device_revoked"
                    await websocket.close(code=4403, reason=disconnect_reason)
                    return
                raise AuthenticationError("device_credential_unavailable")
            if not verify_ecdsa_signature(
                credential["public_key_der"],
                signed_auth_message,
                signature,
            ):
                raise AuthenticationError("invalid_device_authentication")
            if code26_independent_lanes:
                session_id = await runtime_io.call(
                    store.open_transport_session,
                    device_id,
                    negotiated_task_store_protocol,
                )
            else:
                assert payload is not None
                session_id = await runtime_io.call(
                    store.open_device_session, device_id, payload
                )
            server_time = now_seconds()
            recovery_safe_after: int | None = None
            if negotiated_task_store_protocol is not None:
                recovery_safe_after = (
                    server_time
                    if code26_independent_lanes
                    else await runtime_io.call(
                        store.task_recovery_safe_after, device_id, server_time
                    )
                )
            connection = DeviceConnection(
                device_id,
                websocket,
                session_id=session_id,
                task_store_protocol=negotiated_task_store_protocol,
                task_channel_ready=advertised_task_channel_ready,
                task_recovery_safe_after=recovery_safe_after,
                independent_lanes=code26_independent_lanes,
                app_version_code=(
                    transport_auth["app_version_code"]
                    if transport_auth is not None
                    else payload["app_version_code"]
                ),
                send_timeout_seconds=settings.websocket_send_timeout_seconds,
                close_timeout_seconds=settings.websocket_close_timeout_seconds,
            )
            auth_ok: dict[str, Any] = {
                "type": "auth_ok",
                "protocol_version": PROTOCOL_VERSION,
                "device_id": device_id,
                "heartbeat_interval_seconds": 25,
                "server_time": server_time,
            }
            if transport_auth is not None and transport_auth["app_version_code"] >= 27:
                auth_ok["server_session_id"] = session_id
            if negotiated_task_store_protocol is not None:
                auth_ok["task_recovery_safe_after"] = recovery_safe_after
                reconciliation = await _outbox_reconciliation_wire(
                    store, runtime_io, device_id
                )
                if reconciliation is not None:
                    auth_ok["outbox_reconciliation"] = reconciliation
            await connection.send_json(auth_ok)
            await hub.register(connection)
            if advertised_task_channel_ready:
                dispatchable = await runtime_io.call(
                    store.list_dispatchable_tasks, device_id
                )
                for task in dispatchable:
                    await _dispatch_if_fresh(
                        store,
                        hub,
                        runtime_io,
                        task,
                        settings.dispatch_freshness_seconds,
                    )

            while True:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=settings.heartbeat_timeout_seconds
                )
                raw_size = len(raw.encode("utf-8"))
                if raw_size > settings.max_result_bytes:
                    disconnect_reason = "message_too_large"
                    await connection.close(code=4009, reason=disconnect_reason)
                    return
                try:
                    message = _strict_json_loads(raw)
                except (json.JSONDecodeError, SecurityError, ValueError):
                    await connection.send_json(
                        {"type": "error", "protocol_version": PROTOCOL_VERSION, "code": "invalid_json"}
                    )
                    continue
                if not isinstance(message, dict) or message.get("protocol_version") != PROTOCOL_VERSION:
                    await connection.send_json(
                        {
                            "type": "error",
                            "protocol_version": PROTOCOL_VERSION,
                            "code": "invalid_protocol_message",
                        }
                    )
                    continue
                message_type = message.get("type")
                if not await hub.is_current(connection):
                    disconnect_reason = "replaced_by_new_device_session"
                    await connection.close(code=4009, reason=disconnect_reason)
                    return
                if message_type == "heartbeat":
                    heartbeat_kind, heartbeat_payload = validate_heartbeat_message(message)
                    if connection.independent_lanes != (heartbeat_kind == "minimal"):
                        raise SecurityError("heartbeat_contract_mismatch")
                    if heartbeat_kind == "legacy":
                        assert heartbeat_payload is not None
                        heartbeat_task_store_protocol = task_store_protocol(
                            heartbeat_payload["capabilities"]
                        )
                        if heartbeat_task_store_protocol != connection.task_store_protocol:
                            raise SecurityError("task_store_protocol_changed_during_session")
                        heartbeat_task_channel_ready = payload_task_channel_ready(
                            heartbeat_payload["capabilities"]
                        )
                        if connection.task_channel_ready and not heartbeat_task_channel_ready:
                            raise SecurityError("task_channel_ready_cannot_downgrade")
                        if (
                            heartbeat_task_channel_ready
                            and not connection.task_channel_ready
                            and (
                                connection.task_recovery_safe_after is None
                                or now_seconds() < connection.task_recovery_safe_after
                            )
                        ):
                            raise SecurityError("task_recovery_not_yet_safe")
                        await runtime_io.call(
                            store.update_device_heartbeat,
                            session_id,
                            device_id,
                            heartbeat_payload,
                        )
                        if heartbeat_task_channel_ready and not connection.task_channel_ready:
                            await connection.set_task_channel_ready(True)
                    else:
                        await runtime_io.call(
                            store.update_transport_heartbeat, session_id, device_id
                        )
                    connection.mark_client_activity()
                    heartbeat_ack: dict[str, Any] = {
                        "type": "heartbeat_ack",
                        "protocol_version": PROTOCOL_VERSION,
                        "server_time": now_seconds(),
                    }
                    if (
                        heartbeat_kind == "legacy"
                        and connection.task_store_protocol is not None
                    ):
                        reconciliation = await _outbox_reconciliation_wire(
                            store, runtime_io, device_id
                        )
                        if reconciliation is not None:
                            heartbeat_ack["outbox_reconciliation"] = reconciliation
                    await connection.send_json(heartbeat_ack)
                    if connection.task_channel_ready:
                        dispatchable = await runtime_io.call(
                            store.list_dispatchable_tasks, device_id
                        )
                        for task in dispatchable:
                            # A successful websocket send is not delivery proof. If a task
                            # was written into a half-open connection, the next verified
                            # client heartbeat must redeliver the same idempotent envelope
                            # until task_ack/result makes it terminal. The Android outbox
                            # claim prevents duplicate execution.
                            await _dispatch_if_fresh(
                                store,
                                hub,
                                runtime_io,
                                task,
                                settings.dispatch_freshness_seconds,
                            )
                elif message_type == "task_channel_state":
                    if not connection.independent_lanes:
                        raise SecurityError("task_channel_state_not_negotiated")
                    protocol, state, ready, failure_stage = validate_task_channel_state(
                        message
                    )
                    if protocol != connection.task_store_protocol:
                        raise SecurityError("task_store_protocol_changed_during_session")
                    if (
                        ready
                        and not connection.task_channel_ready
                        and (
                            connection.task_recovery_safe_after is None
                            or now_seconds() < connection.task_recovery_safe_after
                        )
                    ):
                        raise SecurityError("task_recovery_not_yet_safe")
                    # DB first on downgrade and Hub second; on upgrade the temporary
                    # DB-ready/Hub-not-ready split also fails closed. No interleaving can
                    # observe both dispatch gates open before the state is fully applied.
                    await runtime_io.call(
                        store.update_task_channel_state,
                        session_id,
                        device_id,
                        protocol,
                        state,
                        ready,
                        failure_stage,
                    )
                    await connection.set_task_channel_ready(ready)
                    connection.mark_client_activity()
                    await connection.send_json(
                        {
                            "type": "task_channel_state_ack",
                            "protocol_version": PROTOCOL_VERSION,
                            "task_store_protocol": protocol,
                            "state": state,
                            "ready": ready,
                            "server_time": now_seconds(),
                        }
                    )
                    if ready:
                        dispatchable = await runtime_io.call(
                            store.list_dispatchable_tasks, device_id
                        )
                        for task in dispatchable:
                            await _dispatch_if_fresh(
                                store,
                                hub,
                                runtime_io,
                                task,
                                settings.dispatch_freshness_seconds,
                            )
                elif message_type == "device_state_update":
                    if not connection.independent_lanes:
                        raise SecurityError("device_state_update_not_negotiated")
                    state, failure_stage, state_payload = validate_device_state_update(
                        message
                    )
                    if state_payload is not None:
                        if state_payload["app_version_code"] < 26:
                            raise SecurityError("code26_device_state_required")
                        if task_store_protocol(
                            state_payload["capabilities"]
                        ) != connection.task_store_protocol:
                            raise SecurityError(
                                "task_store_protocol_changed_during_session"
                            )
                    await runtime_io.call(
                        store.update_device_state,
                        session_id,
                        device_id,
                        state,
                        failure_stage,
                        state_payload,
                    )
                    connection.mark_client_activity()
                    await connection.send_json(
                        {
                            "type": "device_state_ack",
                            "protocol_version": PROTOCOL_VERSION,
                            "state": state,
                            "server_time": now_seconds(),
                        }
                    )
                elif message_type == "task_ack":
                    require_attempt_id = connection.app_version_code >= 28
                    expected_ack_fields = {"type", "protocol_version", "task_id"}
                    if require_attempt_id:
                        expected_ack_fields.add("delivery_attempt_id")
                    if set(message) != expected_ack_fields:
                        raise SecurityError("invalid_task_ack_fields")
                    task_id = safe_text(message.get("task_id"), maximum=80, field="task_id", allow_empty=False)
                    delivery_attempt_id = (
                        safe_text(
                            message.get("delivery_attempt_id"),
                            maximum=80,
                            field="delivery_attempt_id",
                            allow_empty=False,
                        )
                        if require_attempt_id
                        else None
                    )
                    if (
                        connection.task_store_protocol is not None
                        and not connection.task_channel_ready
                    ):
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "task_channel_not_ready",
                                "task_id": task_id,
                            }
                        )
                        continue
                    ack_kind = await runtime_io.call(
                        store.acknowledge_delivery_attempt,
                        device_id,
                        task_id,
                        session_id,
                        delivery_attempt_id,
                        require_attempt_id=require_attempt_id,
                    )
                    if ack_kind is DeliveryAckKind.REJECTED:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "unknown_or_expired_task",
                                "task_id": task_id,
                            }
                        )
                    elif ack_kind is DeliveryAckKind.STALE:
                        # A durable client claim can outlive an earlier delivery
                        # attempt's ACK lease. The current authenticated session may
                        # report that old attempt late; ignore it without advancing
                        # task state or killing a healthy transport.
                        connection.mark_client_activity()
                    else:
                        await hub.acknowledge_task(
                            connection, task_id, delivery_attempt_id
                        )
                        connection.mark_client_activity()
                elif message_type == "task_result":
                    if set(message) - {
                        "type",
                        "protocol_version",
                        "task_id",
                        "status",
                        "result",
                        "error",
                    }:
                        raise SecurityError("invalid_task_result_fields")
                    task_id = safe_text(
                        message.get("task_id"),
                        maximum=80,
                        field="task_id",
                        allow_empty=False,
                    )
                    if (
                        connection.task_store_protocol is not None
                        and not connection.task_channel_ready
                    ):
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "task_channel_not_ready",
                                "task_id": task_id,
                            }
                        )
                        continue
                    status, result, error_code, error_message = _validated_task_result(
                        message
                    )
                    completion = await runtime_io.call(
                        store.complete_task,
                        device_id,
                        task_id,
                        status,
                        result,
                        error_code,
                        error_message,
                        server_session_id=session_id,
                    )
                    if completion.kind is TaskCompletionKind.CONFLICT:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "task_result_conflict",
                                "task_id": task_id,
                            }
                        )
                    elif not completion.acknowledged:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "invalid_task_result",
                                "task_id": task_id,
                            }
                        )
                    else:
                        await hub.confirm_task_result(connection, task_id)
                        connection.mark_client_activity()
                        await connection.send_json(
                            {"type": "task_result_ack", "protocol_version": PROTOCOL_VERSION, "task_id": task_id}
                        )
                elif message_type == "outbox_reconciliation_begin":
                    if set(message) != {
                        "type",
                        "protocol_version",
                        "authorization_id",
                        "challenge",
                        "old_scope",
                        "new_scope",
                        "items",
                    }:
                        raise SecurityError("invalid_reconciliation_begin_fields")
                    authorization_id = safe_text(
                        message.get("authorization_id"),
                        maximum=80,
                        field="authorization_id",
                        allow_empty=False,
                    )
                    challenge = safe_text(
                        message.get("challenge"),
                        maximum=128,
                        field="challenge",
                        allow_empty=False,
                    )
                    old_scope = safe_text(
                        message.get("old_scope"),
                        maximum=80,
                        field="old_scope",
                        allow_empty=False,
                    )
                    new_scope = safe_text(
                        message.get("new_scope"),
                        maximum=80,
                        field="new_scope",
                        allow_empty=False,
                    )
                    items = _validate_reconciliation_manifest(message.get("items"))
                    try:
                        started = await runtime_io.call(
                            store.begin_outbox_reconciliation,
                            device_id,
                            authorization_id,
                            challenge,
                            old_scope,
                            new_scope,
                            items,
                        )
                    except NotFoundError:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "reconciliation_authorization_not_found",
                            }
                        )
                        continue
                    except AuthenticationError:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "reconciliation_not_authorized",
                            }
                        )
                        continue
                    except ConflictError as exc:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": str(exc) or "reconciliation_conflict",
                            }
                        )
                        continue
                    connection.mark_client_activity()
                    await connection.send_json(
                        {
                            "type": "outbox_reconciliation_begin_ack",
                            "protocol_version": PROTOCOL_VERSION,
                            **started,
                        }
                    )
                elif message_type == "outbox_reconciliation_item":
                    common_fields = {
                        "type",
                        "protocol_version",
                        "authorization_id",
                        "challenge",
                        "old_scope",
                        "new_scope",
                        "task_id",
                        "local_state",
                    }
                    local_state = safe_text(
                        message.get("local_state"),
                        maximum=12,
                        field="local_state",
                        allow_empty=False,
                    )
                    if local_state == "RUNNING":
                        if set(message) != common_fields:
                            raise SecurityError("invalid_running_reconciliation_fields")
                    elif local_state == "PENDING":
                        if set(message) - (common_fields | {"status", "result", "error"}):
                            raise SecurityError("invalid_pending_reconciliation_fields")
                    else:
                        raise SecurityError("invalid_reconciliation_local_state")
                    authorization_id = safe_text(
                        message.get("authorization_id"),
                        maximum=80,
                        field="authorization_id",
                        allow_empty=False,
                    )
                    challenge = safe_text(
                        message.get("challenge"),
                        maximum=128,
                        field="challenge",
                        allow_empty=False,
                    )
                    old_scope = safe_text(
                        message.get("old_scope"),
                        maximum=80,
                        field="old_scope",
                        allow_empty=False,
                    )
                    new_scope = safe_text(
                        message.get("new_scope"),
                        maximum=80,
                        field="new_scope",
                        allow_empty=False,
                    )
                    reconciliation_task_id = safe_text(
                        message.get("task_id"),
                        maximum=80,
                        field="task_id",
                        allow_empty=False,
                    )
                    result_sha256 = ""
                    result_bytes = 0
                    try:
                        if local_state == "PENDING":
                            status, result, error_code, error_message = _validated_task_result(
                                message
                            )
                            envelope: dict[str, Any] = {"status": status}
                            if status == "succeeded":
                                envelope["result"] = result
                            else:
                                envelope["error"] = {
                                    "code": error_code,
                                    "message": error_message,
                                }
                            encoded_result = canonical_json(envelope).encode("utf-8")
                            result_sha256 = hashlib.sha256(encoded_result).hexdigest()
                            result_bytes = len(encoded_result)
                            acknowledged = await runtime_io.call(
                                store.reconcile_outbox_pending_result,
                                device_id,
                                authorization_id,
                                challenge,
                                old_scope,
                                new_scope,
                                reconciliation_task_id,
                                result_sha256,
                                result_bytes,
                                status,
                                result,
                                error_code,
                                error_message,
                            )
                        else:
                            acknowledged = await runtime_io.call(
                                store.reconcile_outbox_running,
                                device_id,
                                authorization_id,
                                challenge,
                                old_scope,
                                new_scope,
                                reconciliation_task_id,
                            )
                    except NotFoundError:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "reconciliation_task_not_found",
                                "task_id": reconciliation_task_id,
                            }
                        )
                        continue
                    except AuthenticationError:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "reconciliation_not_authorized",
                                "task_id": reconciliation_task_id,
                            }
                        )
                        continue
                    except ConflictError:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": (
                                    "reconciliation_result_conflict"
                                    if local_state == "PENDING"
                                    else "reconciliation_state_conflict"
                                ),
                                "task_id": reconciliation_task_id,
                            }
                        )
                        continue
                    connection.mark_client_activity()
                    await connection.send_json(
                        {
                            "type": "outbox_reconciliation_item_ack",
                            "protocol_version": PROTOCOL_VERSION,
                            **acknowledged,
                        }
                    )
                elif message_type == "outbox_reconciliation_complete":
                    if set(message) != {
                        "type",
                        "protocol_version",
                        "authorization_id",
                        "challenge",
                        "old_scope",
                        "new_scope",
                    }:
                        raise SecurityError("invalid_reconciliation_complete_fields")
                    authorization_id = safe_text(
                        message.get("authorization_id"),
                        maximum=80,
                        field="authorization_id",
                        allow_empty=False,
                    )
                    challenge = safe_text(
                        message.get("challenge"),
                        maximum=128,
                        field="challenge",
                        allow_empty=False,
                    )
                    old_scope = safe_text(
                        message.get("old_scope"),
                        maximum=80,
                        field="old_scope",
                        allow_empty=False,
                    )
                    new_scope = safe_text(
                        message.get("new_scope"),
                        maximum=80,
                        field="new_scope",
                        allow_empty=False,
                    )
                    try:
                        confirmed = await runtime_io.call(
                            store.confirm_outbox_reconciliation,
                            device_id,
                            authorization_id,
                            challenge,
                            old_scope,
                            new_scope,
                        )
                    except NotFoundError:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "reconciliation_authorization_not_found",
                            }
                        )
                        continue
                    except AuthenticationError:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "reconciliation_not_authorized",
                            }
                        )
                        continue
                    except ConflictError:
                        await connection.send_json(
                            {
                                "type": "error",
                                "protocol_version": PROTOCOL_VERSION,
                                "code": "reconciliation_items_incomplete",
                            }
                        )
                        continue
                    connection.mark_client_activity()
                    await connection.send_json(
                        {
                            "type": "outbox_reconciliation_complete_ack",
                            "protocol_version": PROTOCOL_VERSION,
                            **confirmed,
                        }
                    )
                else:
                    await connection.send_json(
                        {
                            "type": "error",
                            "protocol_version": PROTOCOL_VERSION,
                            "code": "unknown_message_type",
                        }
                    )
        except asyncio.TimeoutError:
            disconnect_reason = "heartbeat_or_auth_timeout"
            if connection:
                await connection.close(code=4008, reason=disconnect_reason)
            else:
                await websocket.close(code=4401, reason="authentication_timeout")
        except WebSocketDisconnect as disconnected:
            disconnect_reason = _websocket_disconnect_reason(
                disconnected.code, getattr(disconnected, "reason", None)
            )
        except (SecurityError, AuthenticationError, NotFoundError, json.JSONDecodeError, KeyError):
            disconnect_reason = "authentication_or_protocol_rejected"
            if connection:
                await connection.close(code=4002, reason=disconnect_reason)
            else:
                await websocket.close(code=4401, reason="authentication_failed")
        except (RuntimeIoBusy, RuntimeIoTimeout):
            disconnect_reason = "runtime_dependency_unavailable"
            if connection:
                await connection.close(code=1013, reason=disconnect_reason)
            else:
                await websocket.close(code=1013, reason=disconnect_reason)
        except RuntimeError as websocket_runtime_error:
            runtime_error = str(websocket_runtime_error)
            if (
                "WebSocket is not connected" not in runtime_error
                and runtime_error
                not in {"websocket_send_timeout", "websocket_send_failed"}
            ):
                raise
            disconnect_reason = (
                runtime_error
                if runtime_error in {"websocket_send_timeout", "websocket_send_failed"}
                else "connection_closed"
            )
        finally:
            if connection is not None:
                await hub.unregister(connection)
                if session_id is not None:
                    try:
                        await runtime_io.call(
                            store.close_device_session,
                            session_id,
                            connection.device_id,
                            disconnect_reason,
                        )
                    except (RuntimeIoBusy, RuntimeIoTimeout):
                        pass

    return app
