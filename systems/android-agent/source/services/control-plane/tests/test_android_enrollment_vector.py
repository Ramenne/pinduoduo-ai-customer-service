from __future__ import annotations

import base64
from copy import deepcopy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization

from oin_control_plane.protocol import normalize_device_payload, validate_device_payload
from oin_control_plane.security import (
    SecurityError,
    b64url_decode,
    b64url_encode,
    canonical_json,
    canonical_payload_digest,
    credential_repair_message,
    verify_ecdsa_signature,
    websocket_auth_message,
    websocket_transport_auth_message,
)


FIXTURE = Path(__file__).parent / "fixtures" / "oin_v1_contract_vector.properties"
REPOSITORY = Path(__file__).resolve().parents[3]
JAVA_PROBE = (
    REPOSITORY
    / "apps"
    / "android-agent"
    / "scripts"
    / "run-protocol-contract-probe.ps1"
)


def _fixture() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in FIXTURE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        assert separator and key not in values
        values[key] = value
    assert values["format"] == "oin-v1-contract-vector-1"
    return values


def _text(values: dict[str, str], key: str) -> str:
    return b64url_decode(values[key], max_bytes=4096).decode("utf-8")


def _raw_payload(values: dict[str, str]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        field: _text(values, f"text.{field}")
        for field in (
            "display_name",
            "installation_id",
            "manufacturer",
            "brand",
            "model",
            "android_version",
            "app_version",
            "kernel_version",
            "timezone",
            "network_type",
        )
    }
    payload["sdk"] = int(values["int.sdk"])
    payload["app_version_code"] = int(values["int.app_version_code"])
    capabilities: dict[str, Any] = {
        field: values[f"bool.{field}"] == "true"
        for field in (
            "accessibility_enabled",
            "accessibility_connected",
            "notification_enabled",
            "notification_connected",
            "battery_optimization_ignored",
            "background_start_user_configured",
            "xianyu_installed",
            "xianyu_rules_verified",
            "screenshot_supported",
            "root_signal",
        )
    }
    for field in ("xianyu_version", "xianyu_rules_version", "local_executor_version"):
        capabilities[field] = _text(values, f"text.{field}")
    capabilities["cloud_task_types"] = values["tasks"].split(",")
    payload["capabilities"] = capabilities
    return payload


def _run_java_probe() -> dict[str, str]:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    assert powershell, "PowerShell is required for the cross-language Java probe"
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(JAVA_PROBE),
            "-VectorPath",
            str(FIXTURE),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.strip().partition("=")
        if separator:
            output[key] = value
    return output


def _diagnostic_payload(values: dict[str, str]) -> dict[str, Any]:
    payload = _raw_payload(values)
    payload["app_version_code"] = 25
    capabilities = payload["capabilities"]
    capabilities.pop("background_start_user_configured")
    capabilities.update(
        {
            "background_start_status": values["diag.background_start_status"],
            "task_store_protocol": int(values["diag.task_store_protocol"]),
            "task_channel_ready": values["diag.task_channel_ready"] == "true",
            "task_store_state": values["diag.task_store_state"],
        }
    )
    integer_fields = {
        "process_started_at_ms",
        "process_pid",
        "service_started_at_ms",
        "last_heartbeat_sent_at_ms",
        "last_heartbeat_ack_at_ms",
        "supervisor_lag_ms",
        "reconnect_attempt",
        "reconnect_deadline_at_ms",
        "socket_epoch",
    }
    payload["diagnostics"] = {
        field: int(values[f"diag.{field}"])
        if field in integer_fields
        else values[f"diag.{field}"]
        for field in (
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
        )
    }
    return payload


def test_android_and_python_execute_one_shared_enrollment_and_ws_contract_vector() -> None:
    values = _fixture()
    raw = _raw_payload(values)
    normalized = normalize_device_payload(raw)

    with pytest.raises(SecurityError, match="pre_normalized"):
        validate_device_payload(raw)
    assert validate_device_payload(normalized) == normalized

    canonical = canonical_json(normalized)
    digest = canonical_payload_digest(normalized)
    message = "\n".join(
        [
            "oin-v1-enroll",
            _text(values, "challenge.id_b64"),
            _text(values, "challenge.nonce_b64"),
            digest,
        ]
    )
    assert canonical == _text(values, "expected.canonical_b64")
    assert digest == values["expected.digest"]
    assert message == _text(values, "expected.message_b64")
    assert json.dumps(
        _text(values, "text.quote_input"), ensure_ascii=False, separators=(",", ":")
    ) == _text(values, "expected.quote_b64")
    integer_vector = {"zero": 0, "one": 1, "minus_one": -1, "max": 2_147_483_647}
    assert canonical_json(integer_vector) == _text(values, "expected.integer_canonical_b64")
    with pytest.raises(SecurityError, match="non_integer"):
        canonical_payload_digest({"float": 1.5})

    java = _run_java_probe()
    assert _text(java, "canonical_b64") == canonical
    assert java["digest"] == digest
    assert _text(java, "message_b64") == message
    assert java["fixture_signature_valid"] == "true"
    assert java["raw_signature_rejected"] == "true"
    assert java["invalid_base64url_rejected"] == "true"
    assert java["non_integer_number_rejected"] == "true"
    assert java["code26_diagnostics_accepted"] == "true"
    assert java["code27_diagnostics_accepted"] == "true"
    assert java["code29_diagnostics_accepted"] == "true"
    assert java["code29_old_diagnostics_rejected"] == "true"
    assert java["code28_new_diagnostics_rejected"] == "true"
    assert java["mixed_code26_diagnostics_rejected"] == "true"

    diagnostic = _diagnostic_payload(values)
    normalized_diagnostic = normalize_device_payload(diagnostic)
    assert validate_device_payload(normalized_diagnostic) == normalized_diagnostic
    diagnostic_canonical = canonical_json(normalized_diagnostic)
    diagnostic_digest = canonical_payload_digest(normalized_diagnostic)
    diagnostic_message = "\n".join(
        [
            "oin-v1-enroll",
            _text(values, "challenge.id_b64"),
            _text(values, "challenge.nonce_b64"),
            diagnostic_digest,
        ]
    )
    assert diagnostic_canonical == _text(values, "expected.diagnostic_canonical_b64")
    assert diagnostic_digest == values["expected.diagnostic_digest"]
    assert diagnostic_message == _text(values, "expected.diagnostic_message_b64")
    assert _text(java, "diagnostic_canonical_b64") == diagnostic_canonical
    assert java["diagnostic_digest"] == diagnostic_digest
    assert _text(java, "diagnostic_message_b64") == diagnostic_message

    public_key_der = b64url_decode(values["ecdsa.public_spki_b64"], max_bytes=512)
    assert verify_ecdsa_signature(
        public_key_der, message, values["ecdsa.signature_der_b64"]
    )
    assert verify_ecdsa_signature(
        public_key_der, message, java["java_signature_der_b64"]
    )
    assert verify_ecdsa_signature(
        public_key_der,
        diagnostic_message,
        values["ecdsa.diagnostic_signature_der_b64"],
    )
    assert verify_ecdsa_signature(
        public_key_der,
        diagnostic_message,
        java["java_diagnostic_signature_der_b64"],
    )
    assert not verify_ecdsa_signature(public_key_der, message, b64url_encode(bytes(64)))

    request_text = _text(java, "request_b64")
    assert f'"payload":{canonical},"signature"' in request_text
    request = json.loads(request_text)
    assert request["payload"] == normalized
    assert verify_ecdsa_signature(public_key_der, message, request["signature"])

    ws_message = websocket_auth_message(
        _text(values, "ws.device_id_b64"),
        _text(values, "challenge.id_b64"),
        _text(values, "challenge.nonce_b64"),
        int(values["ws.issued_at"]),
        normalized,
    )
    assert ws_message == _text(values, "expected.ws_message_b64")
    assert _text(java, "ws_message_b64") == ws_message
    assert verify_ecdsa_signature(
        public_key_der, ws_message, java["java_ws_signature_der_b64"]
    )
    diagnostic_ws_message = websocket_auth_message(
        _text(values, "ws.device_id_b64"),
        _text(values, "challenge.id_b64"),
        _text(values, "challenge.nonce_b64"),
        int(values["ws.issued_at"]),
        normalized_diagnostic,
    )
    assert diagnostic_ws_message == _text(
        values, "expected.diagnostic_ws_message_b64"
    )
    assert _text(java, "diagnostic_ws_message_b64") == diagnostic_ws_message
    assert verify_ecdsa_signature(
        public_key_der,
        diagnostic_ws_message,
        java["java_diagnostic_ws_signature_der_b64"],
    )
    repair_message = credential_repair_message(
        _text(values, "ws.device_id_b64"),
        _text(values, "challenge.id_b64"),
        _text(values, "challenge.nonce_b64"),
        normalized_diagnostic,
    )
    assert repair_message == _text(values, "expected.credential_repair_message_b64")
    assert _text(java, "credential_repair_message_b64") == repair_message
    assert verify_ecdsa_signature(
        public_key_der,
        repair_message,
        java["java_credential_repair_signature_der_b64"],
    )
    ws_request_text = _text(java, "ws_request_b64")
    assert ws_request_text.endswith(f'"payload":{canonical}}}')
    ws_request = json.loads(ws_request_text)
    assert ws_request["payload"] == normalized
    assert verify_ecdsa_signature(public_key_der, ws_message, ws_request["signature"])

    transport_auth = {
        "contract": "code26_transport_v1",
        "app_version_code": 26,
        "task_store_protocol": 2,
    }
    transport_auth_message = websocket_transport_auth_message(
        _text(values, "ws.device_id_b64"),
        _text(values, "challenge.id_b64"),
        _text(values, "challenge.nonce_b64"),
        int(values["ws.issued_at"]),
        transport_auth,
    )
    assert _text(java, "transport_auth_message_b64") == transport_auth_message
    transport_auth_request_text = _text(java, "transport_auth_request_b64")
    transport_auth_request = json.loads(transport_auth_request_text)
    assert set(transport_auth_request) == {
        "type",
        "protocol_version",
        "challenge_id",
        "device_id",
        "signature",
        "transport_auth",
    }
    assert transport_auth_request["transport_auth"] == transport_auth
    assert "payload" not in transport_auth_request
    assert verify_ecdsa_signature(
        public_key_der,
        transport_auth_message,
        java["java_transport_auth_signature_der_b64"],
    )

    with pytest.raises(SecurityError, match="invalid_base64url"):
        b64url_decode("AAEC_w==", max_bytes=16)
    with pytest.raises(SecurityError, match="invalid_base64url"):
        b64url_decode("not+url", max_bytes=16)

    unknown_diagnostic = deepcopy(diagnostic)
    unknown_diagnostic["diagnostics"]["exception_text"] = "must never cross the wire"
    with pytest.raises(SecurityError, match="invalid_device_diagnostic_fields"):
        normalize_device_payload(unknown_diagnostic)
    code26_diagnostic = deepcopy(diagnostic)
    code26_diagnostic["app_version_code"] = 26
    code26_diagnostic["diagnostics"].update(
        {
            "transport_failure_stage": "none",
            "device_state_failure_stage": "device_snapshot_collect",
            "outbox_failure_stage": "outbox_load",
        }
    )
    assert validate_device_payload(
        normalize_device_payload(code26_diagnostic)
    )["diagnostics"]["outbox_failure_stage"] == "outbox_load"
    code27_diagnostic = deepcopy(code26_diagnostic)
    code27_diagnostic["app_version_code"] = 28
    code27_diagnostic["diagnostics"].update(
        {
            "server_session_id": "11111111-1111-4111-8111-111111111111",
            "service_watchdog_lag_ms": 0,
        }
    )
    normalized_code27 = validate_device_payload(
        normalize_device_payload(code27_diagnostic)
    )["diagnostics"]
    assert normalized_code27["server_session_id"] == (
        "11111111-1111-4111-8111-111111111111"
    )
    assert normalized_code27["service_watchdog_lag_ms"] == 0
    code29_diagnostic = deepcopy(code27_diagnostic)
    code29_diagnostic["app_version_code"] = 29
    code29_diagnostic["diagnostics"].update(
        {
            "max_supervisor_lag_ms": 123,
            "max_service_watchdog_lag_ms": 456,
            "last_recovery_started_at_ms": 1_723_200_030_000,
            "last_recovery_completed_at_ms": 1_723_200_035_000,
            "last_recovery_attempts": 2,
            "last_recovery_trigger": "heartbeat_ack_suspect",
            "last_recovery_failure_stage": "websocket_upgrade",
            "last_recovery_outcome": "recovered",
        }
    )
    normalized_code29 = validate_device_payload(
        normalize_device_payload(code29_diagnostic)
    )["diagnostics"]
    assert normalized_code29["max_supervisor_lag_ms"] == 123
    assert normalized_code29["last_recovery_attempts"] == 2
    assert normalized_code29["last_recovery_trigger"] == "heartbeat_ack_suspect"
    assert normalized_code29["last_recovery_failure_stage"] == "websocket_upgrade"
    assert normalized_code29["last_recovery_outcome"] == "recovered"
    code29_old_shape = deepcopy(code27_diagnostic)
    code29_old_shape["app_version_code"] = 29
    with pytest.raises(SecurityError, match="invalid_device_diagnostic_fields"):
        normalize_device_payload(code29_old_shape)
    code28_new_shape = deepcopy(code29_diagnostic)
    code28_new_shape["app_version_code"] = 28
    with pytest.raises(SecurityError, match="invalid_device_diagnostic_fields"):
        normalize_device_payload(code28_new_shape)
    invalid_recovery_enum = deepcopy(code29_diagnostic)
    invalid_recovery_enum["diagnostics"]["last_recovery_outcome"] = "secret_error_text"
    with pytest.raises(SecurityError, match="invalid_last_recovery_outcome"):
        normalize_device_payload(invalid_recovery_enum)
    mixed_code26_diagnostic = deepcopy(diagnostic)
    mixed_code26_diagnostic["diagnostics"]["transport_failure_stage"] = "none"
    with pytest.raises(SecurityError, match="invalid_device_diagnostic_fields"):
        normalize_device_payload(mixed_code26_diagnostic)
    arbitrary_reason = deepcopy(diagnostic)
    arbitrary_reason["diagnostics"]["last_retire_reason"] = "socket failed: token=secret"
    with pytest.raises(SecurityError, match="invalid_last_retire_reason"):
        normalize_device_payload(arbitrary_reason)
    excessive_attempt = deepcopy(diagnostic)
    excessive_attempt["diagnostics"]["reconnect_attempt"] = 10_001
    with pytest.raises(SecurityError, match="reconnect_attempt_out_of_range"):
        normalize_device_payload(excessive_attempt)
    mixed_contract = deepcopy(diagnostic)
    mixed_contract["capabilities"]["background_start_user_configured"] = False
    with pytest.raises(SecurityError, match="unknown_or_missing_capability_fields"):
        normalize_device_payload(mixed_contract)

    # The fixture private key is test-only and proves it is a real P-256 PKCS#8 vector.
    private_key = serialization.load_der_private_key(
        b64url_decode(values["ecdsa.private_pkcs8_b64"], max_bytes=512), password=None
    )
    assert private_key.curve.name == "secp256r1"
