package com.feifei.phonebridge;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.TextUtils;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

final class BridgeState {
    static final String PACKAGE_NAME = "com.feifei.phonebridge";
    static final int PORT = 8765;
    // Fail closed until the persisted operator pause state is loaded off the FGS startup path.
    static final AtomicBoolean PAUSED = new AtomicBoolean(true);
    static volatile boolean accessibilityConnected = false;
    static volatile String foregroundPackage = "";
    static volatile String foregroundClass = "";
    static volatile long lastAccessibilityEventAt = 0L;
    static volatile String lastAccessibilityEventPackage = "";
    static volatile String lastAccessibilityEventClass = "";
    static volatile String lastAccessibilityEventType = "";
    static volatile String lastActiveWindowPackage = "";
    static volatile String lastActiveWindowClass = "";
    static volatile long lastActiveWindowCheckedAt = 0L;

    private static volatile String token;

    private BridgeState() {}

    static synchronized String ensureToken(Context context) {
        if (!TextUtils.isEmpty(token)) {
            return token;
        }
        File tokenFile = new File(context.getFilesDir(), "bridge_token");
        try {
            if (tokenFile.exists()) {
                try (FileInputStream input = new FileInputStream(tokenFile);
                     ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                    byte[] buffer = new byte[128];
                    int read;
                    while ((read = input.read(buffer)) != -1) {
                        bytes.write(buffer, 0, read);
                    }
                    token = new String(bytes.toByteArray(), StandardCharsets.UTF_8).trim();
                }
            }
            if (TextUtils.isEmpty(token)) {
                byte[] bytes = new byte[32];
                new SecureRandom().nextBytes(bytes);
                StringBuilder builder = new StringBuilder(64);
                for (byte value : bytes) {
                    builder.append(String.format(Locale.US, "%02x", value & 0xff));
                }
                token = builder.toString();
                try (FileOutputStream output = new FileOutputStream(tokenFile, false)) {
                    output.write(token.getBytes(StandardCharsets.UTF_8));
                }
            }
            return token;
        } catch (IOException error) {
            throw new IllegalStateException("Unable to initialize bridge token", error);
        }
    }

    static void initialize(Context context) {
        if (BuildConfig.ENABLE_LOCAL_DIAGNOSTIC_BRIDGE) {
            ensureToken(context);
        }
        SharedPreferences preferences = context.getSharedPreferences("bridge_state", Context.MODE_PRIVATE);
        PAUSED.set(preferences.getBoolean("paused", false));
    }

    static void setPaused(Context context, boolean paused) {
        PAUSED.set(paused);
        context.getSharedPreferences("bridge_state", Context.MODE_PRIVATE)
                .edit()
                .putBoolean("paused", paused)
                .apply();
    }

    static boolean isAccessibilityEnabled(Context context) {
        String enabled = Settings.Secure.getString(
                context.getContentResolver(),
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        );
        if (enabled == null) {
            return false;
        }
        String expected = PACKAGE_NAME + "/" + BridgeAccessibilityService.class.getName();
        for (String component : enabled.split(":")) {
            if (component.equalsIgnoreCase(expected)) {
                return true;
            }
        }
        return false;
    }

    static JSONObject status(Context context) throws JSONException {
        JSONObject json = new JSONObject();
        json.put("ok", true);
        json.put("version", BuildConfig.VERSION_NAME);
        json.put("port", PORT);
        json.put("diagnostic_bridge_enabled", BuildConfig.ENABLE_LOCAL_DIAGNOSTIC_BRIDGE);
        json.put("bind_address", BuildConfig.LOCAL_DIAGNOSTIC_BIND_ADDRESS);
        json.put("auth_required", true);
        json.put("transport_security", "token_over_plain_http");
        json.put("paused", PAUSED.get());
        json.put("accessibility_enabled", isAccessibilityEnabled(context));
        json.put("accessibility_connected", accessibilityConnected);
        json.put("notification_connected", false);
        json.put("foreground_package", foregroundPackage);
        json.put("foreground_class", foregroundClass);
        json.put("last_accessibility_event_at", lastAccessibilityEventAt);
        json.put("last_accessibility_event_package", lastAccessibilityEventPackage);
        json.put("last_accessibility_event_class", lastAccessibilityEventClass);
        json.put("last_accessibility_event_type", lastAccessibilityEventType);
        json.put("last_active_window_package", lastActiveWindowPackage);
        json.put("last_active_window_class", lastActiveWindowClass);
        json.put("last_active_window_checked_at", lastActiveWindowCheckedAt);
        json.put("control_service_running", ControlService.isRunning());
        json.put("cloud_wake_lock_held", ControlService.isWakeLockHeld());
        long elapsed = SystemClock.elapsedRealtime();
        json.put("cloud_process_instance_id", CloudState.processInstanceId(context));
        json.put("cloud_process_started_at", CloudState.processStartedAt(context));
        json.put("cloud_process_pid", CloudState.processPid(context));
        json.put("cloud_service_instance_id", CloudState.serviceInstanceId(context));
        json.put("cloud_service_started_at", CloudState.serviceStartedAt(context));
        json.put("cloud_last_heartbeat_sent_at", CloudState.lastHeartbeatSentAt(context));
        json.put("cloud_last_heartbeat_sent_epoch", CloudState.lastHeartbeatSentEpoch(context));
        json.put("cloud_last_heartbeat_ack_at", CloudState.lastHeartbeatAcknowledgedAt(context));
        json.put("cloud_last_heartbeat_ack_epoch", CloudState.lastHeartbeatAckEpoch(context));
        json.put("cloud_supervisor_tick_age_ms",
                CloudState.supervisorTickAgeMillis(context, elapsed));
        json.put("cloud_supervisor_tick_lag_ms", CloudState.supervisorTickLagMillis(context));
        json.put("cloud_supervisor_next_retry_in_ms",
                CloudState.supervisorNextRetryInMillis(context, elapsed));
        json.put("cloud_supervisor_reason", CloudState.supervisorReason(context));
        json.put("cloud_reconnect_attempt", CloudState.reconnectAttempt(context));
        json.put("cloud_reconnect_reason", CloudState.reconnectReason(context));
        json.put("cloud_socket_epoch", CloudState.socketEpoch(context));
        json.put("cloud_last_retire_reason", CloudState.lastRetireReason(context));
        json.put("cloud_last_retire_at", CloudState.lastRetireAt(context));
        json.put("cloud_last_retire_epoch", CloudState.lastRetireEpoch(context));
        json.put("xianyu_execution_environment", XianyuExecutionEnvironment.capture(context).toJson());
        return json;
    }
}
