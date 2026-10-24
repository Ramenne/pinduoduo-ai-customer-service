from __future__ import annotations

import uuid
import base64
import hashlib
import re
from typing import Any

from .security import SecurityError, canonical_json, safe_integer, safe_text


PROTOCOL_VERSION = "1"
TASK_STORE_PROTOCOL_V2 = 2
TRANSPORT_AUTH_CONTRACT_V1 = "code26_transport_v1"
DEFAULT_TASK_TTL_SECONDS = 60
ALLOWED_TASK_TYPES = frozenset({"health", "observe", "xianyu_snapshot", "action"})
ALLOWED_CLOUD_ACTIONS = frozenset(
    {"tap_free_brighten", "launch_app", "back", "home", "click_node", "scroll_page", "click_unique_node", "tap_unique_node", "set_node_text", "import_image", "set_description_text"}
)
ALLOWED_NODE_SELECTOR_KINDS = frozenset({"resource_id", "text", "description"})
ALLOWED_SCROLL_DIRECTIONS = frozenset({"up", "down"})
XIANYU_PACKAGE = "com.taobao.idlefish"
TASK_STORE_STATES = frozenset({"ready", "recovery_pending", "unavailable", "not_bound"})
BACKGROUND_START_STATUSES = frozenset({"unknown", "enabled", "disabled"})
LIFECYCLE_STATES = frozenset(
    {
        "UNBOUND",
        "CONFIG_REQUIRED",
        "BOUND_OFFLINE",
        "CONNECTING",
        "ONLINE_FRESH",
        "SUSPECT",
        "RECONNECT_WAIT",
        "REVOKED",
    }
)
RETIRE_REASONS = frozenset(
    {
        "never",
        "network_changed",
        "network_unavailable",
        "cloud_liveness_probe_unavailable",
        "manual_reconnect",
        "heartbeat_ack_grace_exhausted",
        "cloud_auth_timeout",
        "service_stopped",
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
        "device_private_key_unavailable",
        "device_private_key_corrupt",
        "device_keystore_unavailable",
        "cloud_connection_failed",
        "cloud_closed",
        "authentication_or_protocol_rejected",
    }
)
LEGACY_DIAGNOSTIC_FIELDS = frozenset(
    {
        "process_instance_id",
        "process_started_at_ms",
        "process_pid",
        "service_instance_id",
        "service_started_at_ms",
        "last_heartbeat_sent_at_ms",
        "last_heartbeat_ack_at_ms",
        "supervisor_lag_ms",
        "lifecycle_state",
        "last_retire_reason",
        "reconnect_attempt",
        "reconnect_deadline_at_ms",
        "socket_epoch",
    }
)
CODE26_DIAGNOSTIC_FIELDS = LEGACY_DIAGNOSTIC_FIELDS | {
    "transport_failure_stage",
    "device_state_failure_stage",
    "outbox_failure_stage",
}
CODE27_DIAGNOSTIC_FIELDS = CODE26_DIAGNOSTIC_FIELDS | {
    "server_session_id",
    "service_watchdog_lag_ms",
}
CODE29_DIAGNOSTIC_FIELDS = CODE27_DIAGNOSTIC_FIELDS | {
    "max_supervisor_lag_ms",
    "max_service_watchdog_lag_ms",
    "last_recovery_started_at_ms",
    "last_recovery_completed_at_ms",
    "last_recovery_attempts",
    "last_recovery_trigger",
    "last_recovery_failure_stage",
    "last_recovery_outcome",
}
RECOVERY_TRIGGERS = frozenset(
    {
        "never",
        "heartbeat_ack_suspect",
        "websocket_closed",
        "transport_failure",
        "network_available",
    }
)
RECOVERY_FAILURE_STAGES = frozenset(
    {
        "none",
        "dns",
        "tcp_connect",
        "tls",
        "http_redirect",
        "websocket_upgrade",
        "websocket_closed",
        "authentication",
        "network_unavailable",
        "device_revoked",
        "device_unbound",
        "config_required",
        "recovery_budget",
        "unknown",
    }
)
RECOVERY_OUTCOMES = frozenset(
    {"never", "in_progress", "recovered", "budget_exhausted", "blocked"}
)
TRANSPORT_FAILURE_STAGES = frozenset(
    {
        "none",
        "heartbeat_scheduler",
        "heartbeat_send_rejected",
        "websocket_exception",
    }
)
DEVICE_STATE_FAILURE_STAGES = frozenset(
    {
        "none",
        "device_snapshot_collect",
        "device_snapshot_normalize",
        "device_snapshot_serialize",
        "device_snapshot_send",
        "device_state_cache_store",
    }
)
OUTBOX_FAILURE_STAGES = frozenset(
    {"none", "outbox_load", "outbox_replay", "outbox_send"}
)
TASK_CHANNEL_STATES = frozenset({"ready", "recovery_pending", "unavailable"})


def bounded_input_text(value: Any, maximum: int = 5000) -> str:
    """Preserve exact user input, including line breaks, for device readback."""
    if not isinstance(value, str) or not value or any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise SecurityError("invalid_input_text")
    try:
        length = len(value.encode('utf-16-le')) // 2
    except UnicodeError as exc:
        raise SecurityError("invalid_input_text") from exc
    if length > maximum:
        raise SecurityError("input_text_too_long")
    return value

DEVICE_INFO_FIELDS = frozenset(
    {
        "display_name",
        "installation_id",
        "manufacturer",
        "brand",
        "model",
        "android_version",
        "sdk",
        "app_version",
        "app_version_code",
        "kernel_version",
        "timezone",
        "network_type",
        "capabilities",
        "diagnostics",
    }
)

COMMON_CAPABILITY_FIELDS = frozenset(
    {
        "accessibility_enabled",
        "accessibility_connected",
        "notification_enabled",
        "notification_connected",
        "battery_optimization_ignored",
        "xianyu_installed",
        "xianyu_version",
        "xianyu_rules_version",
        "xianyu_rules_verified",
        "screenshot_supported",
        "root_signal",
        "local_executor_version",
        "cloud_task_types",
    }
)
LEGACY_CAPABILITY_FIELDS = COMMON_CAPABILITY_FIELDS | {
    "background_start_user_configured"
}
V2_CAPABILITY_FIELDS = COMMON_CAPABILITY_FIELDS | {
    "background_start_status",
    "task_store_protocol",
    "task_channel_ready",
    "task_store_state",
}


def normalize_device_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SecurityError("device_payload_must_be_object")
    unknown = set(value) - DEVICE_INFO_FIELDS
    if unknown:
        raise SecurityError("unknown_device_payload_fields")

    capabilities = validate_capabilities(value.get("capabilities"))
    app_version_code = safe_integer(
        value.get("app_version_code"),
        minimum=1,
        maximum=2_147_483_647,
        field="app_version_code",
    )
    payload: dict[str, Any] = {
        "display_name": safe_text(
            value.get("display_name", ""), maximum=80, field="display_name", allow_empty=False
        ),
        "installation_id": safe_text(
            value.get("installation_id", ""), maximum=80, field="installation_id", allow_empty=False
        ),
        "manufacturer": safe_text(
            value.get("manufacturer", ""), maximum=80, field="manufacturer", allow_empty=False
        ),
        "brand": safe_text(value.get("brand", ""), maximum=80, field="brand", allow_empty=False),
        "model": safe_text(value.get("model", ""), maximum=120, field="model", allow_empty=False),
        "android_version": safe_text(
            value.get("android_version", ""), maximum=40, field="android_version", allow_empty=False
        ),
        "sdk": safe_integer(value.get("sdk"), minimum=28, maximum=1000, field="sdk"),
        "app_version": safe_text(
            value.get("app_version", ""), maximum=64, field="app_version", allow_empty=False
        ),
        "app_version_code": app_version_code,
        "kernel_version": safe_text(
            value.get("kernel_version", ""), maximum=64, field="kernel_version", allow_empty=False
        ),
        "timezone": safe_text(value.get("timezone", ""), maximum=80, field="timezone", allow_empty=False),
        "network_type": safe_text(
            value.get("network_type", ""), maximum=32, field="network_type", allow_empty=False
        ),
        "capabilities": capabilities,
    }
    if "diagnostics" in value:
        payload["diagnostics"] = validate_diagnostics(
            value["diagnostics"], app_version_code=app_version_code
        )
    return payload


def validate_diagnostics(value: Any, *, app_version_code: int) -> dict[str, Any]:
    fields = frozenset(value) if isinstance(value, dict) else frozenset()
    if app_version_code >= 29:
        expected_fields = CODE29_DIAGNOSTIC_FIELDS
    elif app_version_code >= 27:
        expected_fields = CODE27_DIAGNOSTIC_FIELDS
    elif app_version_code == 26:
        expected_fields = CODE26_DIAGNOSTIC_FIELDS
    else:
        expected_fields = LEGACY_DIAGNOSTIC_FIELDS
    if not isinstance(value, dict) or fields != expected_fields:
        raise SecurityError("invalid_device_diagnostic_fields")

    def instance_id(field: str) -> str:
        text = safe_text(value.get(field), maximum=36, field=field, allow_empty=False)
        if text == "unknown":
            return text
        try:
            parsed = uuid.UUID(text)
        except (ValueError, AttributeError) as exc:
            raise SecurityError(f"invalid_{field}") from exc
        if str(parsed) != text.lower():
            raise SecurityError(f"noncanonical_{field}")
        return text.lower()

    lifecycle_state = safe_text(
        value.get("lifecycle_state"),
        maximum=32,
        field="lifecycle_state",
        allow_empty=False,
    )
    if lifecycle_state not in LIFECYCLE_STATES:
        raise SecurityError("invalid_lifecycle_state")
    last_retire_reason = safe_text(
        value.get("last_retire_reason"),
        maximum=64,
        field="last_retire_reason",
        allow_empty=False,
    )
    if last_retire_reason not in RETIRE_REASONS:
        raise SecurityError("invalid_last_retire_reason")
    maximum_time_ms = 10_000_000_000_000
    result = {
        "process_instance_id": instance_id("process_instance_id"),
        "process_started_at_ms": safe_integer(
            value.get("process_started_at_ms"),
            minimum=0,
            maximum=maximum_time_ms,
            field="process_started_at_ms",
        ),
        "process_pid": safe_integer(
            value.get("process_pid"), minimum=0, maximum=10_000_000, field="process_pid"
        ),
        "service_instance_id": instance_id("service_instance_id"),
        "service_started_at_ms": safe_integer(
            value.get("service_started_at_ms"),
            minimum=0,
            maximum=maximum_time_ms,
            field="service_started_at_ms",
        ),
        "last_heartbeat_sent_at_ms": safe_integer(
            value.get("last_heartbeat_sent_at_ms"),
            minimum=0,
            maximum=maximum_time_ms,
            field="last_heartbeat_sent_at_ms",
        ),
        "last_heartbeat_ack_at_ms": safe_integer(
            value.get("last_heartbeat_ack_at_ms"),
            minimum=0,
            maximum=maximum_time_ms,
            field="last_heartbeat_ack_at_ms",
        ),
        "supervisor_lag_ms": safe_integer(
            value.get("supervisor_lag_ms"),
            minimum=0,
            maximum=86_400_000,
            field="supervisor_lag_ms",
        ),
        "lifecycle_state": lifecycle_state,
        "last_retire_reason": last_retire_reason,
        "reconnect_attempt": safe_integer(
            value.get("reconnect_attempt"),
            minimum=0,
            maximum=10_000,
            field="reconnect_attempt",
        ),
        "reconnect_deadline_at_ms": safe_integer(
            value.get("reconnect_deadline_at_ms"),
            minimum=0,
            maximum=maximum_time_ms,
            field="reconnect_deadline_at_ms",
        ),
        "socket_epoch": safe_integer(
            value.get("socket_epoch"),
            minimum=0,
            maximum=9_007_199_254_740_991,
            field="socket_epoch",
        ),
    }
    if fields in {
        CODE26_DIAGNOSTIC_FIELDS,
        CODE27_DIAGNOSTIC_FIELDS,
        CODE29_DIAGNOSTIC_FIELDS,
    }:
        for field, allowed in {
            "transport_failure_stage": TRANSPORT_FAILURE_STAGES,
            "device_state_failure_stage": DEVICE_STATE_FAILURE_STAGES,
            "outbox_failure_stage": OUTBOX_FAILURE_STAGES,
        }.items():
            stage = safe_text(
                value.get(field), maximum=40, field=field, allow_empty=False
            )
            if stage not in allowed:
                raise SecurityError(f"invalid_{field}")
            result[field] = stage
    if fields in {CODE27_DIAGNOSTIC_FIELDS, CODE29_DIAGNOSTIC_FIELDS}:
        result["server_session_id"] = instance_id("server_session_id")
        result["service_watchdog_lag_ms"] = safe_integer(
            value.get("service_watchdog_lag_ms"),
            minimum=0,
            maximum=86_400_000,
            field="service_watchdog_lag_ms",
        )
    if fields == CODE29_DIAGNOSTIC_FIELDS:
        result["max_supervisor_lag_ms"] = safe_integer(
            value.get("max_supervisor_lag_ms"),
            minimum=0,
            maximum=86_400_000,
            field="max_supervisor_lag_ms",
        )
        result["max_service_watchdog_lag_ms"] = safe_integer(
            value.get("max_service_watchdog_lag_ms"),
            minimum=0,
            maximum=86_400_000,
            field="max_service_watchdog_lag_ms",
        )
        result["last_recovery_started_at_ms"] = safe_integer(
            value.get("last_recovery_started_at_ms"),
            minimum=0,
            maximum=maximum_time_ms,
            field="last_recovery_started_at_ms",
        )
        result["last_recovery_completed_at_ms"] = safe_integer(
            value.get("last_recovery_completed_at_ms"),
            minimum=0,
            maximum=maximum_time_ms,
            field="last_recovery_completed_at_ms",
        )
        result["last_recovery_attempts"] = safe_integer(
            value.get("last_recovery_attempts"),
            minimum=0,
            maximum=10_000,
            field="last_recovery_attempts",
        )
        for field, allowed in {
            "last_recovery_trigger": RECOVERY_TRIGGERS,
            "last_recovery_failure_stage": RECOVERY_FAILURE_STAGES,
            "last_recovery_outcome": RECOVERY_OUTCOMES,
        }.items():
            item = safe_text(
                value.get(field), maximum=40, field=field, allow_empty=False
            )
            if item not in allowed:
                raise SecurityError(f"invalid_{field}")
            result[field] = item
    return result


def validate_device_payload(value: Any) -> dict[str, Any]:
    """Validate the exact signed/wire payload without silently rewriting it."""

    payload = normalize_device_payload(value)
    if canonical_json(value) != canonical_json(payload):
        raise SecurityError("device_payload_must_be_pre_normalized")
    return payload


def validate_transport_auth(value: Any) -> dict[str, Any]:
    """Validate code26's exact signed transport-only authentication contract."""

    if not isinstance(value, dict) or set(value) != {
        "contract",
        "app_version_code",
        "task_store_protocol",
    }:
        raise SecurityError("invalid_transport_auth_fields")
    contract = safe_text(
        value.get("contract"), maximum=40, field="transport_auth_contract", allow_empty=False
    )
    if contract != TRANSPORT_AUTH_CONTRACT_V1:
        raise SecurityError("unsupported_transport_auth_contract")
    app_version_code = safe_integer(
        value.get("app_version_code"),
        minimum=26,
        maximum=2_147_483_647,
        field="app_version_code",
    )
    protocol = safe_integer(
        value.get("task_store_protocol"),
        minimum=TASK_STORE_PROTOCOL_V2,
        maximum=TASK_STORE_PROTOCOL_V2,
        field="task_store_protocol",
    )
    return {
        "contract": contract,
        "app_version_code": app_version_code,
        "task_store_protocol": protocol,
    }


def validate_capabilities(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SecurityError("capabilities_must_be_object")
    task_store_v2 = "task_store_protocol" in value
    expected_fields = V2_CAPABILITY_FIELDS if task_store_v2 else LEGACY_CAPABILITY_FIELDS
    if set(value) != expected_fields:
        raise SecurityError("unknown_or_missing_capability_fields")

    required_boolean_fields = {
        "accessibility_enabled",
        "accessibility_connected",
        "notification_enabled",
        "notification_connected",
        "battery_optimization_ignored",
        "xianyu_installed",
        "xianyu_rules_verified",
        "screenshot_supported",
        "root_signal",
    }
    result: dict[str, Any] = {}
    for field in required_boolean_fields:
        item = value.get(field)
        if not isinstance(item, bool):
            raise SecurityError(f"{field}_must_be_boolean")
        result[field] = item

    if not task_store_v2:
        legacy_background = value["background_start_user_configured"]
        if not isinstance(legacy_background, bool):
            raise SecurityError("background_start_user_configured_must_be_boolean")
        result["background_start_user_configured"] = legacy_background
    else:
        background_status = safe_text(
            value["background_start_status"],
            maximum=32,
            field="background_start_status",
            allow_empty=False,
        )
        if background_status not in BACKGROUND_START_STATUSES:
            raise SecurityError("invalid_background_start_status")
        result["background_start_status"] = background_status

    if task_store_v2:
        task_store_protocol = safe_integer(
            value["task_store_protocol"],
            minimum=TASK_STORE_PROTOCOL_V2,
            maximum=TASK_STORE_PROTOCOL_V2,
            field="task_store_protocol",
        )
        task_channel_ready = value["task_channel_ready"]
        if not isinstance(task_channel_ready, bool):
            raise SecurityError("task_channel_ready_must_be_boolean")
        task_store_state = safe_text(
            value["task_store_state"],
            maximum=32,
            field="task_store_state",
            allow_empty=False,
        )
        if task_store_state not in TASK_STORE_STATES:
            raise SecurityError("invalid_task_store_state")
        if task_channel_ready != (task_store_state == "ready"):
            raise SecurityError("inconsistent_task_store_state")
        result["task_store_protocol"] = task_store_protocol
        result["task_channel_ready"] = task_channel_ready
        result["task_store_state"] = task_store_state

    for field, maximum in {
        "xianyu_version": 64,
        "xianyu_rules_version": 64,
        "local_executor_version": 64,
    }.items():
        result[field] = safe_text(value.get(field, ""), maximum=maximum, field=field)

    task_types = value.get("cloud_task_types")
    if not isinstance(task_types, list) or any(not isinstance(item, str) for item in task_types):
        raise SecurityError("cloud_task_types_must_be_text_array")
    normalized_tasks = sorted(set(task_types))
    if set(normalized_tasks) - ALLOWED_TASK_TYPES:
        raise SecurityError("unsupported_cloud_task_type")
    result["cloud_task_types"] = normalized_tasks
    return result


def task_store_protocol(capabilities: dict[str, Any]) -> int | None:
    value = capabilities.get("task_store_protocol")
    return TASK_STORE_PROTOCOL_V2 if value == TASK_STORE_PROTOCOL_V2 else None


def task_channel_ready(capabilities: dict[str, Any]) -> bool:
    """Legacy clients retain their v1 dispatch contract; protocol 2 must opt in."""

    if task_store_protocol(capabilities) is None:
        return True
    return capabilities.get("task_channel_ready") is True


def validate_heartbeat_message(value: Any) -> tuple[str, dict[str, Any] | None]:
    """Accept either the exact code26 transport heartbeat or the code25 payload form."""

    if not isinstance(value, dict):
        raise SecurityError("heartbeat_must_be_object")
    fields = set(value)
    if fields == {"type", "protocol_version"}:
        return "minimal", None
    if fields == {"type", "protocol_version", "payload"}:
        return "legacy", validate_device_payload(value.get("payload"))
    raise SecurityError("invalid_heartbeat_fields")


def validate_task_channel_state(value: Any) -> tuple[int, str, bool, str]:
    if not isinstance(value, dict) or set(value) != {
        "type",
        "protocol_version",
        "task_store_protocol",
        "state",
        "ready",
        "failure_stage",
    }:
        raise SecurityError("invalid_task_channel_state_fields")
    protocol = safe_integer(
        value.get("task_store_protocol"),
        minimum=TASK_STORE_PROTOCOL_V2,
        maximum=TASK_STORE_PROTOCOL_V2,
        field="task_store_protocol",
    )
    state = safe_text(
        value.get("state"), maximum=32, field="task_channel_state", allow_empty=False
    )
    if state not in TASK_CHANNEL_STATES:
        raise SecurityError("invalid_task_channel_state")
    ready = value.get("ready")
    if not isinstance(ready, bool):
        raise SecurityError("task_channel_ready_must_be_boolean")
    if ready != (state == "ready"):
        raise SecurityError("inconsistent_task_channel_state")
    failure_stage = safe_text(
        value.get("failure_stage"),
        maximum=40,
        field="outbox_failure_stage",
        allow_empty=False,
    )
    if failure_stage not in OUTBOX_FAILURE_STAGES:
        raise SecurityError("invalid_outbox_failure_stage")
    if ready and failure_stage != "none":
        raise SecurityError("ready_task_channel_has_failure_stage")
    return protocol, state, ready, failure_stage


def validate_device_state_update(
    value: Any,
) -> tuple[str, str, dict[str, Any] | None]:
    if not isinstance(value, dict):
        raise SecurityError("device_state_update_must_be_object")
    state = safe_text(
        value.get("state"), maximum=16, field="device_state", allow_empty=False
    )
    fields = set(value)
    if state == "fresh":
        if fields != {
            "type",
            "protocol_version",
            "state",
            "failure_stage",
            "payload",
        }:
            raise SecurityError("invalid_fresh_device_state_fields")
        failure_stage = safe_text(
            value.get("failure_stage"),
            maximum=40,
            field="device_state_failure_stage",
            allow_empty=False,
        )
        if failure_stage != "none":
            raise SecurityError("fresh_device_state_has_failure_stage")
        return state, failure_stage, validate_device_payload(value.get("payload"))
    if state == "stale":
        if fields != {"type", "protocol_version", "state", "failure_stage"}:
            raise SecurityError("invalid_stale_device_state_fields")
        failure_stage = safe_text(
            value.get("failure_stage"),
            maximum=40,
            field="device_state_failure_stage",
            allow_empty=False,
        )
        if failure_stage not in DEVICE_STATE_FAILURE_STAGES - {"none"}:
            raise SecurityError("invalid_device_state_failure_stage")
        return state, failure_stage, None
    raise SecurityError("invalid_device_state")


def validate_task_request(task_type: Any, parameters: Any, ttl_seconds: Any) -> tuple[str, dict[str, Any], int]:
    if not isinstance(task_type, str) or task_type not in ALLOWED_TASK_TYPES:
        raise SecurityError("unknown_task_type")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise SecurityError("task_parameters_must_be_object")
    if task_type == "action":
        action = safe_text(
            parameters.get("action"), maximum=32, field="action", allow_empty=False
        )
        if action not in ALLOWED_CLOUD_ACTIONS:
            raise SecurityError("unsupported_cloud_action")
        if action == "launch_app":
            if set(parameters) != {"action", "package"}:
                raise SecurityError("invalid_launch_app_parameters")
            package_name = safe_text(
                parameters.get("package"), maximum=120, field="package", allow_empty=False
            )
            if package_name != XIANYU_PACKAGE:
                raise SecurityError("cloud_launch_package_not_allowed")
            normalized_parameters = {"action": action, "package": package_name}
        elif action == "tap_free_brighten":
            if set(parameters)!={'action','package'} or parameters.get('package')!=XIANYU_PACKAGE:
                raise SecurityError('invalid_free_brighten_parameters')
            normalized_parameters={'action':action,'package':XIANYU_PACKAGE}
        elif action == "set_description_text":
            if set(parameters) != {"action", "package", "value"} or parameters.get("package") != XIANYU_PACKAGE:
                raise SecurityError("invalid_description_parameters")
            normalized_parameters={"action":action,"package":XIANYU_PACKAGE,
                                   "value":bounded_input_text(parameters.get("value"))}
        elif action in {"click_node", "click_unique_node", "tap_unique_node", "set_node_text"}:
            expected = {"action", "package", "selector"}
            if action == "set_node_text":
                expected.add("value")
            if set(parameters) != expected:
                raise SecurityError("invalid_click_node_parameters")
            package_name = safe_text(
                parameters.get("package"), maximum=120, field="package", allow_empty=False
            )
            if package_name != XIANYU_PACKAGE:
                raise SecurityError("cloud_click_package_not_allowed")
            selector = parameters.get("selector")
            if not isinstance(selector, dict) or set(selector) != {"kind", "value"}:
                raise SecurityError("invalid_node_selector")
            selector_kind = safe_text(
                selector.get("kind"), maximum=32, field="selector_kind", allow_empty=False
            )
            if selector_kind not in ALLOWED_NODE_SELECTOR_KINDS and not (action=="set_node_text" and selector_kind=="class" and selector.get("value")=="android.widget.EditText"):
                raise SecurityError("invalid_node_selector_kind")
            if action in {"click_unique_node", "tap_unique_node", "set_node_text"}:
                selector_value = bounded_input_text(selector.get("value"), 500)
                if not selector_value.strip():
                    raise SecurityError("selector_value_required")
            else:
                selector_value = safe_text(
                    selector.get("value"), maximum=500, field="selector_value", allow_empty=False
                )
            normalized_parameters = {
                "action": action,
                "package": package_name,
                "selector": {"kind": selector_kind, "value": selector_value},
            }
            if action == "set_node_text":
                normalized_parameters["value"] = bounded_input_text(parameters.get("value"))
        elif action == "import_image":
            if set(parameters) != {"action", "package", "mime_type", "filename", "sha256", "data_base64"}:
                raise SecurityError("invalid_image_parameters")
            if parameters.get("package") != XIANYU_PACKAGE:
                raise SecurityError("cloud_image_package_not_allowed")
            encoded = parameters.get("data_base64")
            digest = parameters.get("sha256")
            mime = parameters.get("mime_type")
            if not isinstance(encoded, str) or len(encoded) > 349528:
                raise SecurityError("image_byte_limit")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise SecurityError("invalid_image_sha256")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise SecurityError("invalid_image_encoding") from exc
            if not raw or len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != digest:
                raise SecurityError("image_integrity_failed")
            signatures = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
            if mime not in signatures or not raw.startswith(signatures[mime]):
                raise SecurityError("invalid_image_format")
            extension = "jpg" if mime == "image/jpeg" else "png"
            if parameters.get("filename") != f"oin-{digest[:16]}.{extension}":
                raise SecurityError("invalid_image_filename")
            normalized_parameters = dict(parameters)
        elif action == "scroll_page":
            if set(parameters) != {"action", "package", "direction"}:
                raise SecurityError("invalid_scroll_page_parameters")
            package_name = safe_text(
                parameters.get("package"), maximum=120, field="package", allow_empty=False
            )
            if package_name != XIANYU_PACKAGE:
                raise SecurityError("cloud_scroll_package_not_allowed")
            direction = safe_text(
                parameters.get("direction"), maximum=16, field="direction", allow_empty=False
            )
            if direction not in ALLOWED_SCROLL_DIRECTIONS:
                raise SecurityError("invalid_scroll_direction")
            normalized_parameters = {
                "action": action,
                "package": package_name,
                "direction": direction,
            }
        else:
            if set(parameters) != {"action"}:
                raise SecurityError("invalid_global_action_parameters")
            normalized_parameters = {"action": action}
    else:
        if parameters:
            raise SecurityError("read_only_tasks_do_not_accept_parameters")
        normalized_parameters = {}
    # TTL is a business-freshness boundary, not a transport recovery SLA. A
    # current-page read may intentionally expire before an OEM-frozen process
    # recovers; only work that is still live when the channel becomes ready is
    # eligible for redispatch.
    ttl = safe_integer(
        ttl_seconds,
        minimum=10,
        maximum=300,
        field="ttl_seconds",
    )
    return task_type, normalized_parameters, ttl
