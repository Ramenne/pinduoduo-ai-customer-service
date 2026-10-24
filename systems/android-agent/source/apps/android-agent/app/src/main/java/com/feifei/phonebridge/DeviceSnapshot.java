package com.feifei.phonebridge;

import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Build;
import android.os.PowerManager;

import com.feifei.phonebridge.plugin.xianyu.XianYuPlugin;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import java.util.TimeZone;

final class DeviceSnapshot {
    private static final String XIANYU_PACKAGE = "com.taobao.idlefish";
    private static final Set<String> DIAGNOSTIC_RETIRE_REASONS = Collections.unmodifiableSet(
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

    private DeviceSnapshot() {
    }

    static JSONObject create(Context context) throws JSONException {
        Context app = context.getApplicationContext();
        JSONObject payload = new JSONObject();
        payload.put("display_name", DevicePreferences.displayName(app));
        payload.put("installation_id", DeviceIdentity.installationId(app));
        payload.put("manufacturer", nonEmpty(Build.MANUFACTURER, "unknown"));
        payload.put("brand", nonEmpty(Build.BRAND, "unknown"));
        payload.put("model", nonEmpty(Build.MODEL, "unknown"));
        payload.put("android_version", nonEmpty(Build.VERSION.RELEASE, "unknown"));
        payload.put("sdk", Build.VERSION.SDK_INT);
        payload.put("app_version", BuildConfig.VERSION_NAME);
        payload.put("app_version_code", BuildConfig.VERSION_CODE);
        payload.put("kernel_version", BuildConfig.VERSION_NAME);
        payload.put("timezone", TimeZone.getDefault().getID());
        payload.put("network_type", networkType(app));
        payload.put("capabilities", capabilities(app));
        payload.put("diagnostics", diagnostics(app));
        return payload;
    }

    static JSONObject createCanonical(Context context) throws Exception {
        return new JSONObject(DevicePayloadNormalizer.normalize(CanonicalJson.toMap(create(context))));
    }

    private static JSONObject capabilities(Context context) throws JSONException {
        JSONObject capabilities = new JSONObject();
        boolean xianyuInstalled = packageInfo(context, XIANYU_PACKAGE) != null;
        PackageInfo xianyu = packageInfo(context, XIANYU_PACKAGE);
        NotificationManager notificationManager = context.getSystemService(NotificationManager.class);
        String taskStoreState = taskStoreState(context);
        capabilities.put("accessibility_enabled", BridgeState.isAccessibilityEnabled(context));
        capabilities.put("accessibility_connected", BridgeState.accessibilityConnected);
        capabilities.put(
                "notification_enabled",
                notificationManager != null && notificationManager.areNotificationsEnabled()
        );
        capabilities.put("notification_connected", false);
        capabilities.put("battery_optimization_ignored", batteryOptimizationIgnored(context));
        capabilities.put("background_start_status", "unknown");
        capabilities.put("xianyu_installed", xianyuInstalled);
        capabilities.put("xianyu_version", xianyu == null || xianyu.versionName == null ? "" : xianyu.versionName);
        capabilities.put("xianyu_rules_version", XianYuPlugin.VERSION);
        capabilities.put("xianyu_rules_verified", false);
        capabilities.put("screenshot_supported", false);
        capabilities.put("root_signal", hasRootSignal());
        capabilities.put("local_executor_version", BuildConfig.VERSION_NAME);
        capabilities.put("task_store_protocol", 2);
        capabilities.put("task_channel_ready", TaskResultStore.READY.equals(taskStoreState));
        capabilities.put("task_store_state", taskStoreState);
        JSONArray tasks = new JSONArray();
        tasks.put("health");
        tasks.put("observe");
        tasks.put("xianyu_snapshot");
        tasks.put("action");
        capabilities.put("cloud_task_types", tasks);
        return capabilities;
    }

    private static JSONObject diagnostics(Context context) throws JSONException {
        JSONObject diagnostics = new JSONObject();
        diagnostics.put("process_instance_id", CloudState.processInstanceId(context));
        diagnostics.put("process_started_at_ms", boundedLong(
                CloudState.processStartedAt(context), 10_000_000_000_000L));
        diagnostics.put("process_pid", boundedInt(CloudState.processPid(context), 10_000_000));
        diagnostics.put("service_instance_id", CloudState.serviceInstanceId(context));
        diagnostics.put("service_started_at_ms", boundedLong(
                CloudState.serviceStartedAt(context), 10_000_000_000_000L));
        diagnostics.put(
                "last_heartbeat_sent_at_ms",
                boundedLong(CloudState.lastHeartbeatSentAt(context), 10_000_000_000_000L)
        );
        diagnostics.put(
                "last_heartbeat_ack_at_ms",
                boundedLong(
                        CloudState.lastHeartbeatAcknowledgedAt(context),
                        10_000_000_000_000L
                )
        );
        diagnostics.put(
                "supervisor_lag_ms",
                boundedLong(CloudState.supervisorTickLagMillis(context), 86_400_000L)
        );
        diagnostics.put("lifecycle_state", diagnosticLifecycleState(context));
        diagnostics.put(
                "last_retire_reason",
                diagnosticRetireReason(CloudState.lastRetireReason(context))
        );
        diagnostics.put("reconnect_attempt", boundedInt(
                CloudState.reconnectAttempt(context), 10_000));
        diagnostics.put(
                "reconnect_deadline_at_ms",
                boundedLong(CloudState.nextReconnectAt(context), 10_000_000_000_000L)
        );
        diagnostics.put("socket_epoch", boundedLong(
                CloudState.socketEpoch(context), 9_007_199_254_740_991L));
        diagnostics.put(
                "transport_failure_stage",
                CloudState.transportFailureStage(context)
        );
        diagnostics.put(
                "device_state_failure_stage",
                CloudState.deviceStateFailureStage(context)
        );
        diagnostics.put(
                "outbox_failure_stage",
                CloudState.outboxFailureStage(context)
        );
        diagnostics.put("server_session_id", CloudState.serverSessionId(context));
        diagnostics.put(
                "service_watchdog_lag_ms",
                boundedLong(CloudState.serviceWatchdogLagMillis(context), 86_400_000L)
        );
        diagnostics.put("max_supervisor_lag_ms", boundedLong(
                CloudState.maxSupervisorTickLagMillis(context), 86_400_000L));
        diagnostics.put("max_service_watchdog_lag_ms", boundedLong(
                CloudState.maxServiceWatchdogLagMillis(context), 86_400_000L));
        diagnostics.put("last_recovery_started_at_ms", boundedLong(
                CloudState.recoveryStartedAt(context), 10_000_000_000_000L));
        diagnostics.put("last_recovery_completed_at_ms", boundedLong(
                CloudState.recoveryCompletedAt(context), 10_000_000_000_000L));
        diagnostics.put("last_recovery_attempts", boundedInt(
                CloudState.recoveryAttempts(context), 10_000));
        diagnostics.put("last_recovery_trigger", CloudState.recoveryTrigger(context));
        diagnostics.put("last_recovery_failure_stage",
                CloudState.recoveryFailureStage(context));
        diagnostics.put("last_recovery_outcome", CloudState.recoveryOutcome(context));
        return diagnostics;
    }

    private static long boundedLong(long value, long maximum) {
        return Math.min(maximum, Math.max(0L, value));
    }

    private static int boundedInt(int value, int maximum) {
        return Math.min(maximum, Math.max(0, value));
    }

    private static String diagnosticLifecycleState(Context context) {
        String state = CloudState.connectionState(context);
        switch (state) {
            case CloudState.UNBOUND:
            case CloudState.CONFIG_REQUIRED:
            case CloudState.BOUND_OFFLINE:
            case CloudState.CONNECTING:
            case CloudState.ONLINE_FRESH:
            case CloudState.SUSPECT:
            case CloudState.RECONNECT_WAIT:
            case CloudState.REVOKED:
                return state;
            default:
                return CloudState.isBound(context)
                        ? CloudState.BOUND_OFFLINE
                        : CloudState.UNBOUND;
        }
    }

    private static String diagnosticRetireReason(String value) {
        if (DIAGNOSTIC_RETIRE_REASONS.contains(value)) {
            return value;
        }
        if (value != null && value.startsWith("cloud_closed_")) {
            return "cloud_closed";
        }
        return "authentication_or_protocol_rejected";
    }

    private static String taskStoreState(Context context) {
        if (!CloudState.isBound(context)) {
            return TaskResultStore.NOT_BOUND;
        }
        String state = CloudState.outboxState(context);
        if (TaskResultStore.READY.equals(state) || TaskResultStore.RECOVERY_PENDING.equals(state) ||
                TaskResultStore.UNAVAILABLE.equals(state)) {
            return state;
        }
        return TaskResultStore.UNAVAILABLE;
    }

    private static PackageInfo packageInfo(Context context, String packageName) {
        try {
            return context.getPackageManager().getPackageInfo(packageName, 0);
        } catch (PackageManager.NameNotFoundException missing) {
            return null;
        }
    }

    private static boolean batteryOptimizationIgnored(Context context) {
        PowerManager manager = context.getSystemService(PowerManager.class);
        return manager != null && manager.isIgnoringBatteryOptimizations(context.getPackageName());
    }

    private static String networkType(Context context) {
        ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
        if (manager == null) {
            return "unknown";
        }
        Network network = manager.getActiveNetwork();
        NetworkCapabilities capabilities = network == null ? null : manager.getNetworkCapabilities(network);
        if (capabilities == null) {
            return "offline";
        }
        if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
            return "wifi";
        }
        if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) {
            return "cellular";
        }
        if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) {
            return "ethernet";
        }
        if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) {
            return "vpn";
        }
        return "other";
    }

    static boolean hasRootSignal() {
        return new File("/system/bin/su").exists() ||
                new File("/system/xbin/su").exists() ||
                new File("/sbin/su").exists();
    }

    private static String nonEmpty(String value, String fallback) {
        return value == null || value.trim().isEmpty() ? fallback : value.trim();
    }
}
