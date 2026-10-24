package com.feifei.phonebridge;

import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;

final class DevicePayloadNormalizer {
    private static final String[] DEVICE_TEXT_FIELDS = {
            "display_name", "installation_id", "manufacturer", "brand", "model",
            "android_version", "app_version", "kernel_version", "timezone", "network_type"
    };
    private static final String[] COMMON_CAPABILITY_BOOLEAN_FIELDS = {
            "accessibility_enabled", "accessibility_connected", "notification_enabled",
            "notification_connected", "battery_optimization_ignored",
            "xianyu_installed", "xianyu_rules_verified",
            "screenshot_supported", "root_signal"
    };
    private static final String[] CAPABILITY_TEXT_FIELDS = {
            "xianyu_version", "xianyu_rules_version", "local_executor_version"
    };
    private static final Set<String> ALLOWED_TASK_TYPES = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList("action", "health", "observe", "xianyu_snapshot"))
    );
    private static final Set<String> LIFECYCLE_STATES = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList(
                    "UNBOUND", "CONFIG_REQUIRED", "BOUND_OFFLINE", "CONNECTING",
                    "ONLINE_FRESH", "SUSPECT", "RECONNECT_WAIT", "REVOKED"
            ))
    );
    private static final Set<String> RETIRE_REASONS = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList(
                    "never", "network_changed", "network_unavailable",
                    "cloud_liveness_probe_unavailable", "manual_reconnect",
                    "heartbeat_ack_grace_exhausted", "cloud_auth_timeout",
                    "service_stopped", "heartbeat_failed", "task_ack_send_rejected",
                    "pending_task_result_delivery_reconnect",
                    "task_result_delivery_reconnect", "task_result_resend_reconnect",
                    "task_executor_rejection_delivery_reconnect",
                    "task_rejection_replay_reconnect",
                    "task_rejection_delivery_reconnect", "task_rejection_send_failed",
                    "outbox_reconciliation_delivery_reconnect",
                    "cloud_protocol_rejected", "device_identity_unavailable",
                    "device_private_key_unavailable", "device_private_key_corrupt",
                    "device_keystore_unavailable", "cloud_connection_failed",
                    "cloud_closed", "authentication_or_protocol_rejected"
            ))
    );
    private static final Set<String> LEGACY_DIAGNOSTIC_FIELDS = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList(
                    "process_instance_id", "process_started_at_ms", "process_pid",
                    "service_instance_id", "service_started_at_ms",
                    "last_heartbeat_sent_at_ms", "last_heartbeat_ack_at_ms",
                    "supervisor_lag_ms", "lifecycle_state", "last_retire_reason",
                    "reconnect_attempt", "reconnect_deadline_at_ms", "socket_epoch"
            ))
    );
    private static final Set<String> CODE26_DIAGNOSTIC_FIELDS;
    private static final Set<String> CODE27_DIAGNOSTIC_FIELDS;
    private static final Set<String> CODE29_DIAGNOSTIC_FIELDS;
    private static final Set<String> DEVICE_FIELDS;
    private static final Set<String> LEGACY_CAPABILITY_FIELDS;
    private static final Set<String> V2_CAPABILITY_FIELDS;

    static {
        Set<String> deviceFields = new HashSet<>(Arrays.asList(DEVICE_TEXT_FIELDS));
        deviceFields.add("sdk");
        deviceFields.add("app_version_code");
        deviceFields.add("capabilities");
        DEVICE_FIELDS = Collections.unmodifiableSet(deviceFields);
        Set<String> commonCapabilityFields = new HashSet<>(
                Arrays.asList(COMMON_CAPABILITY_BOOLEAN_FIELDS)
        );
        commonCapabilityFields.addAll(Arrays.asList(CAPABILITY_TEXT_FIELDS));
        commonCapabilityFields.add("cloud_task_types");
        Set<String> legacyCapabilityFields = new HashSet<>(commonCapabilityFields);
        legacyCapabilityFields.add("background_start_user_configured");
        LEGACY_CAPABILITY_FIELDS = Collections.unmodifiableSet(legacyCapabilityFields);
        Set<String> v2CapabilityFields = new HashSet<>(commonCapabilityFields);
        v2CapabilityFields.add("background_start_status");
        v2CapabilityFields.add("task_store_protocol");
        v2CapabilityFields.add("task_channel_ready");
        v2CapabilityFields.add("task_store_state");
        V2_CAPABILITY_FIELDS = Collections.unmodifiableSet(v2CapabilityFields);
        Set<String> code26DiagnosticFields = new HashSet<>(LEGACY_DIAGNOSTIC_FIELDS);
        code26DiagnosticFields.add("transport_failure_stage");
        code26DiagnosticFields.add("device_state_failure_stage");
        code26DiagnosticFields.add("outbox_failure_stage");
        CODE26_DIAGNOSTIC_FIELDS = Collections.unmodifiableSet(code26DiagnosticFields);
        Set<String> code27DiagnosticFields = new HashSet<>(CODE26_DIAGNOSTIC_FIELDS);
        code27DiagnosticFields.add("server_session_id");
        code27DiagnosticFields.add("service_watchdog_lag_ms");
        CODE27_DIAGNOSTIC_FIELDS = Collections.unmodifiableSet(code27DiagnosticFields);
        Set<String> code29DiagnosticFields = new HashSet<>(CODE27_DIAGNOSTIC_FIELDS);
        code29DiagnosticFields.addAll(Arrays.asList(
                "max_supervisor_lag_ms", "max_service_watchdog_lag_ms",
                "last_recovery_started_at_ms", "last_recovery_completed_at_ms",
                "last_recovery_attempts", "last_recovery_trigger",
                "last_recovery_failure_stage", "last_recovery_outcome"
        ));
        CODE29_DIAGNOSTIC_FIELDS = Collections.unmodifiableSet(code29DiagnosticFields);
    }

    private DevicePayloadNormalizer() {
    }

    static String normalizeDisplayName(String value) {
        return normalizeText(value, 80, false, "display_name");
    }

    static Map<String, Object> normalize(Map<String, Object> raw) {
        if (raw == null) {
            throw new IllegalArgumentException("unknown_or_missing_device_payload_fields");
        }
        Set<String> expectedDeviceFields = new HashSet<>(DEVICE_FIELDS);
        if (raw.containsKey("diagnostics")) {
            expectedDeviceFields.add("diagnostics");
        }
        if (!raw.keySet().equals(expectedDeviceFields)) {
            throw new IllegalArgumentException("unknown_or_missing_device_payload_fields");
        }
        Map<String, Object> normalized = new LinkedHashMap<>();
        for (String field : DEVICE_TEXT_FIELDS) {
            normalized.put(field, normalizeDeviceText(field, requireString(raw, field)));
        }
        normalized.put("sdk", requireInteger(raw, "sdk", 28, 1000));
        normalized.put(
                "app_version_code",
                requireInteger(raw, "app_version_code", 1, Integer.MAX_VALUE)
        );
        int appVersionCode = (Integer) normalized.get("app_version_code");

        Map<String, Object> rawCapabilities = requireMap(raw, "capabilities");
        boolean taskStoreV2 = rawCapabilities.containsKey("task_store_protocol");
        Set<String> expectedCapabilityFields = taskStoreV2
                ? V2_CAPABILITY_FIELDS
                : LEGACY_CAPABILITY_FIELDS;
        if (!rawCapabilities.keySet().equals(expectedCapabilityFields)) {
            throw new IllegalArgumentException("unknown_or_missing_capability_fields");
        }
        Map<String, Object> capabilities = new LinkedHashMap<>();
        for (String field : COMMON_CAPABILITY_BOOLEAN_FIELDS) {
            capabilities.put(field, requireBoolean(rawCapabilities, field));
        }
        if (taskStoreV2) {
            String backgroundStartStatus = requireEnum(
                    rawCapabilities,
                    "background_start_status",
                    "unknown",
                    "enabled",
                    "disabled"
            );
            int taskStoreProtocol = requireInteger(
                    rawCapabilities,
                    "task_store_protocol",
                    2,
                    2
            );
            boolean taskChannelReady = requireBoolean(rawCapabilities, "task_channel_ready");
            String taskStoreState = requireEnum(
                    rawCapabilities,
                    "task_store_state",
                    "ready",
                    "recovery_pending",
                    "unavailable",
                    "not_bound"
            );
            if (taskChannelReady != "ready".equals(taskStoreState)) {
                throw new IllegalArgumentException("task_channel_ready_state_mismatch");
            }
            capabilities.put("background_start_status", backgroundStartStatus);
            capabilities.put("task_store_protocol", taskStoreProtocol);
            capabilities.put("task_channel_ready", taskChannelReady);
            capabilities.put("task_store_state", taskStoreState);
        } else {
            capabilities.put(
                    "background_start_user_configured",
                    requireBoolean(rawCapabilities, "background_start_user_configured")
            );
        }
        for (String field : CAPABILITY_TEXT_FIELDS) {
            capabilities.put(field, normalizeText(requireString(rawCapabilities, field), 64, true, field));
        }
        Object rawTasks = rawCapabilities.get("cloud_task_types");
        if (!(rawTasks instanceof List)) {
            throw new IllegalArgumentException("cloud_task_types_must_be_array");
        }
        TreeSet<String> tasks = new TreeSet<>();
        for (Object task : (List<?>) rawTasks) {
            if (!(task instanceof String) || !ALLOWED_TASK_TYPES.contains(task)) {
                throw new IllegalArgumentException("unsupported_cloud_task_type");
            }
            tasks.add((String) task);
        }
        capabilities.put("cloud_task_types", new ArrayList<>(tasks));
        normalized.put("capabilities", capabilities);
        if (raw.containsKey("diagnostics")) {
            normalized.put("diagnostics", normalizeDiagnostics(
                    requireMap(raw, "diagnostics"), appVersionCode));
        }
        return normalized;
    }

    private static Map<String, Object> normalizeDiagnostics(
            Map<String, Object> raw,
            int appVersionCode
    ) {
        boolean code29 = appVersionCode >= 29;
        boolean code27Or28 = appVersionCode >= 27 && appVersionCode <= 28;
        boolean code26 = appVersionCode == 26;
        Set<String> expected = code29
                ? CODE29_DIAGNOSTIC_FIELDS
                : code27Or28
                        ? CODE27_DIAGNOSTIC_FIELDS
                        : code26 ? CODE26_DIAGNOSTIC_FIELDS : LEGACY_DIAGNOSTIC_FIELDS;
        if (!raw.keySet().equals(expected)) {
            throw new IllegalArgumentException("invalid_device_diagnostic_fields");
        }
        Map<String, Object> diagnostics = new LinkedHashMap<>();
        diagnostics.put(
                "process_instance_id",
                requireInstanceId(raw, "process_instance_id")
        );
        diagnostics.put(
                "process_started_at_ms",
                requireLong(raw, "process_started_at_ms", 0L, 10_000_000_000_000L)
        );
        diagnostics.put("process_pid", requireInteger(raw, "process_pid", 0, 10_000_000));
        diagnostics.put(
                "service_instance_id",
                requireInstanceId(raw, "service_instance_id")
        );
        diagnostics.put(
                "service_started_at_ms",
                requireLong(raw, "service_started_at_ms", 0L, 10_000_000_000_000L)
        );
        diagnostics.put(
                "last_heartbeat_sent_at_ms",
                requireLong(raw, "last_heartbeat_sent_at_ms", 0L, 10_000_000_000_000L)
        );
        diagnostics.put(
                "last_heartbeat_ack_at_ms",
                requireLong(raw, "last_heartbeat_ack_at_ms", 0L, 10_000_000_000_000L)
        );
        diagnostics.put(
                "supervisor_lag_ms",
                requireLong(raw, "supervisor_lag_ms", 0L, 86_400_000L)
        );
        String lifecycleState = requireString(raw, "lifecycle_state");
        if (!LIFECYCLE_STATES.contains(lifecycleState)) {
            throw new IllegalArgumentException("invalid_lifecycle_state");
        }
        diagnostics.put("lifecycle_state", lifecycleState);
        String retireReason = requireString(raw, "last_retire_reason");
        if (!RETIRE_REASONS.contains(retireReason)) {
            throw new IllegalArgumentException("invalid_last_retire_reason");
        }
        diagnostics.put("last_retire_reason", retireReason);
        diagnostics.put(
                "reconnect_attempt",
                requireInteger(raw, "reconnect_attempt", 0, 10_000)
        );
        diagnostics.put(
                "reconnect_deadline_at_ms",
                requireLong(raw, "reconnect_deadline_at_ms", 0L, 10_000_000_000_000L)
        );
        diagnostics.put(
                "socket_epoch",
                requireLong(raw, "socket_epoch", 0L, 9_007_199_254_740_991L)
        );
        if (appVersionCode >= 26) {
            diagnostics.put(
                    "transport_failure_stage",
                    requireEnum(
                            raw,
                            "transport_failure_stage",
                            "none",
                            "heartbeat_scheduler",
                            "heartbeat_send_rejected",
                            "websocket_exception"
                    )
            );
            diagnostics.put(
                    "device_state_failure_stage",
                    requireEnum(
                            raw,
                            "device_state_failure_stage",
                            "none",
                            "device_snapshot_collect",
                            "device_snapshot_normalize",
                            "device_snapshot_serialize",
                            "device_snapshot_send",
                            "device_state_cache_store"
                    )
            );
            diagnostics.put(
                    "outbox_failure_stage",
                    requireEnum(
                            raw,
                            "outbox_failure_stage",
                            "none",
                            "outbox_load",
                            "outbox_replay",
                            "outbox_send"
                    )
            );
        }
        if (appVersionCode >= 27) {
            diagnostics.put(
                    "server_session_id",
                    requireInstanceId(raw, "server_session_id")
            );
            diagnostics.put(
                    "service_watchdog_lag_ms",
                    requireLong(raw, "service_watchdog_lag_ms", 0L, 86_400_000L)
            );
        }
        if (code29) {
            diagnostics.put("max_supervisor_lag_ms", requireLong(
                    raw, "max_supervisor_lag_ms", 0L, 86_400_000L));
            diagnostics.put("max_service_watchdog_lag_ms", requireLong(
                    raw, "max_service_watchdog_lag_ms", 0L, 86_400_000L));
            diagnostics.put("last_recovery_started_at_ms", requireLong(
                    raw, "last_recovery_started_at_ms", 0L, 10_000_000_000_000L));
            diagnostics.put("last_recovery_completed_at_ms", requireLong(
                    raw, "last_recovery_completed_at_ms", 0L, 10_000_000_000_000L));
            diagnostics.put("last_recovery_attempts", requireInteger(
                    raw, "last_recovery_attempts", 0, 10_000));
            diagnostics.put("last_recovery_trigger", requireEnum(raw,
                    "last_recovery_trigger", "never", "heartbeat_ack_suspect",
                    "websocket_closed", "transport_failure", "network_available"));
            diagnostics.put("last_recovery_failure_stage", requireEnum(raw,
                    "last_recovery_failure_stage", "none", "dns", "tcp_connect", "tls",
                    "http_redirect", "websocket_upgrade", "websocket_closed",
                    "authentication", "network_unavailable", "device_revoked",
                    "device_unbound", "config_required", "recovery_budget", "unknown"));
            diagnostics.put("last_recovery_outcome", requireEnum(raw,
                    "last_recovery_outcome", "never", "in_progress", "recovered",
                    "budget_exhausted", "blocked"));
        }
        return diagnostics;
    }

    private static String requireInstanceId(Map<String, Object> values, String field) {
        String value = requireString(values, field);
        if ("unknown".equals(value)) {
            return value;
        }
        try {
            String canonical = UUID.fromString(value).toString();
            if (!canonical.equals(value.toLowerCase(java.util.Locale.ROOT))) {
                throw new IllegalArgumentException("noncanonical_" + field);
            }
            return canonical;
        } catch (IllegalArgumentException invalid) {
            throw new IllegalArgumentException("invalid_" + field, invalid);
        }
    }

    private static String normalizeDeviceText(String field, String value) {
        int maximum;
        switch (field) {
            case "display_name":
            case "installation_id":
            case "manufacturer":
            case "brand":
            case "timezone":
                maximum = 80;
                break;
            case "model":
                maximum = 120;
                break;
            case "android_version":
                maximum = 40;
                break;
            case "app_version":
            case "kernel_version":
                maximum = 64;
                break;
            case "network_type":
                maximum = 32;
                break;
            default:
                throw new IllegalArgumentException("unknown_device_text_field");
        }
        return normalizeText(value, maximum, false, field);
    }

    private static String normalizeText(String value, int maximum, boolean allowEmpty, String field) {
        String normalized = stripPythonWhitespace(Normalizer.normalize(value, Normalizer.Form.NFKC));
        if (!allowEmpty && normalized.isEmpty()) {
            throw new IllegalArgumentException(field + "_required");
        }
        if (normalized.codePointCount(0, normalized.length()) > maximum) {
            throw new IllegalArgumentException(field + "_invalid");
        }
        for (int index = 0; index < normalized.length(); ) {
            int point = normalized.codePointAt(index);
            if (point < 32 || point >= 0xd800 && point <= 0xdfff) {
                throw new IllegalArgumentException(field + "_invalid");
            }
            index += Character.charCount(point);
        }
        return normalized;
    }

    private static String stripPythonWhitespace(String value) {
        int start = 0;
        int end = value.length();
        while (start < end) {
            int codePoint = value.codePointAt(start);
            if (!isPythonWhitespace(codePoint)) {
                break;
            }
            start += Character.charCount(codePoint);
        }
        while (end > start) {
            int codePoint = value.codePointBefore(end);
            if (!isPythonWhitespace(codePoint)) {
                break;
            }
            end -= Character.charCount(codePoint);
        }
        return value.substring(start, end);
    }

    private static boolean isPythonWhitespace(int codePoint) {
        return (codePoint >= 0x0009 && codePoint <= 0x000d) ||
                (codePoint >= 0x001c && codePoint <= 0x0020) ||
                codePoint == 0x0085 || codePoint == 0x00a0 || codePoint == 0x1680 ||
                (codePoint >= 0x2000 && codePoint <= 0x200a) ||
                codePoint == 0x2028 || codePoint == 0x2029 || codePoint == 0x202f ||
                codePoint == 0x205f || codePoint == 0x3000;
    }

    private static String requireString(Map<String, Object> values, String field) {
        Object value = values.get(field);
        if (!(value instanceof String)) {
            throw new IllegalArgumentException(field + "_must_be_text");
        }
        return (String) value;
    }

    private static int requireInteger(
            Map<String, Object> values,
            String field,
            int minimum,
            int maximum
    ) {
        Object value = values.get(field);
        if (!(value instanceof Byte || value instanceof Short || value instanceof Integer || value instanceof Long)) {
            throw new IllegalArgumentException(field + "_must_be_integer");
        }
        long integer = ((Number) value).longValue();
        if (integer < minimum || integer > maximum) {
            throw new IllegalArgumentException(field + "_out_of_range");
        }
        return (int) integer;
    }

    private static long requireLong(
            Map<String, Object> values,
            String field,
            long minimum,
            long maximum
    ) {
        Object value = values.get(field);
        if (!(value instanceof Byte || value instanceof Short || value instanceof Integer || value instanceof Long)) {
            throw new IllegalArgumentException(field + "_must_be_integer");
        }
        long integer = ((Number) value).longValue();
        if (integer < minimum || integer > maximum) {
            throw new IllegalArgumentException(field + "_out_of_range");
        }
        return integer;
    }

    private static boolean requireBoolean(Map<String, Object> values, String field) {
        Object value = values.get(field);
        if (!(value instanceof Boolean)) {
            throw new IllegalArgumentException(field + "_must_be_boolean");
        }
        return (Boolean) value;
    }

    private static String requireEnum(
            Map<String, Object> values,
            String field,
            String... allowed
    ) {
        String value = requireString(values, field);
        for (String candidate : allowed) {
            if (candidate.equals(value)) {
                return value;
            }
        }
        throw new IllegalArgumentException(field + "_invalid");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> requireMap(Map<String, Object> values, String field) {
        Object value = values.get(field);
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException(field + "_must_be_object");
        }
        return (Map<String, Object>) value;
    }
}
