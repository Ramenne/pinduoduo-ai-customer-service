package com.feifei.phonebridge;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.UUID;

final class CloudState {
    static final String UNBOUND = "UNBOUND";
    static final String CONFIG_REQUIRED = "CONFIG_REQUIRED";
    static final String BOUND_OFFLINE = CloudConnectionLifecycle.BOUND_OFFLINE;
    static final String CONNECTING = CloudConnectionLifecycle.CONNECTING;
    static final String ONLINE_FRESH = CloudConnectionLifecycle.ONLINE_FRESH;
    static final String SUSPECT = CloudConnectionLifecycle.SUSPECT;
    static final String RECONNECT_WAIT = CloudConnectionLifecycle.RECONNECT_WAIT;
    static final String REVOKED = "REVOKED";

    private static final String PREFS = "oin_cloud_state";
    private static final String DEVICE_ID = "device_id";
    private static final String ACCOUNT_NAME = "account_name";
    private static final String CONNECTION_STATE = "connection_state";
    private static final String LAST_ERROR_CODE = "last_error_code";
    private static final String LAST_CONNECTED_AT = "last_connected_at";
    private static final String LAST_SERVER_ACTIVITY_AT = "last_server_activity_at";
    private static final String LAST_HEARTBEAT_ACK_AT = "last_heartbeat_ack_at";
    private static final String OUTBOX_STATE = "outbox_state";
    private static final String OUTBOX_PENDING = "outbox_pending";
    private static final String TASK_STORE_ERROR_CODE = "task_store_error_code";
    private static final String TASK_STORE_LEGACY_BYTES = "task_store_legacy_bytes";
    private static final String TASK_STORE_LEGACY_SHA256 = "task_store_legacy_sha256";
    private static final String TASK_RECOVERY_SAFE_AFTER = "task_recovery_safe_after";
    private static final String SERVICE_STATE = "service_state";
    private static final String LAST_RECONNECT_CODE = "last_reconnect_code";
    private static final String LAST_RECONNECT_AT = "last_reconnect_at";
    private static final String PROCESS_INSTANCE_ID = "process_instance_id";
    private static final String PROCESS_STARTED_AT = "process_started_at";
    private static final String PROCESS_PID = "process_pid";
    private static final String PREVIOUS_PROCESS_EXIT = "previous_process_exit";
    private static final String SERVICE_INSTANCE_ID = "service_instance_id";
    private static final String SERVICE_STARTED_AT = "service_started_at";
    private static final String LAST_HEARTBEAT_SENT_AT = "last_heartbeat_sent_at";
    private static final String LAST_HEARTBEAT_SENT_EPOCH = "last_heartbeat_sent_epoch";
    private static final String LAST_HEARTBEAT_ACK_EPOCH = "last_heartbeat_ack_epoch";
    private static final String SERVER_SESSION_ID = "server_session_id";
    private static final String LAST_SERVICE_WATCHDOG_TICK_AT =
            "last_service_watchdog_tick_at";
    private static final String LAST_SERVICE_WATCHDOG_TICK_LAG =
            "last_service_watchdog_tick_lag";
    private static final String LAST_SUPERVISOR_TICK_AT = "last_supervisor_tick_at";
    private static final String LAST_SUPERVISOR_TICK_LAG = "last_supervisor_tick_lag";
    private static final String SUPERVISOR_REASON = "supervisor_reason";
    private static final String NEXT_RECONNECT_AT = "next_reconnect_at";
    private static final String RECONNECT_ATTEMPT = "reconnect_attempt";
    private static final String RECONNECT_REASON = "reconnect_reason";
    private static final String SOCKET_EPOCH = "socket_epoch";
    private static final String LAST_RETIRE_REASON = "last_retire_reason";
    private static final String LAST_RETIRE_AT = "last_retire_at";
    private static final String LAST_RETIRE_EPOCH = "last_retire_epoch";
    private static final String TRANSPORT_FAILURE_STAGE = "transport_failure_stage";
    private static final String DEVICE_STATE_STATUS = "device_state_status";
    private static final String DEVICE_STATE_FAILURE_STAGE = "device_state_failure_stage";
    private static final String DEVICE_STATE_CONFIRMED_AT = "device_state_confirmed_at";
    private static final String OUTBOX_FAILURE_STAGE = "outbox_failure_stage";
    private static final String RECOVERY_STARTED_AT = "recovery_started_at";
    private static final String RECOVERY_COMPLETED_AT = "recovery_completed_at";
    private static final String RECOVERY_ATTEMPTS = "recovery_attempts";
    private static final String RECOVERY_TRIGGER = "recovery_trigger";
    private static final String RECOVERY_FAILURE_STAGE = "recovery_failure_stage";
    private static final String RECOVERY_OUTCOME = "recovery_outcome";
    private static final String MAX_SUPERVISOR_LAG = "max_supervisor_lag";
    private static final String MAX_SERVICE_WATCHDOG_LAG = "max_service_watchdog_lag";
    private static final String PENDING_IDENTITY_ACTIVATION_ALIAS =
            "pending_identity_activation_alias";
    private static final String PROCESS_INSTANCE_VALUE = UUID.randomUUID().toString();
    private static final long PROCESS_STARTED_AT_VALUE = System.currentTimeMillis();
    private static final long EXPECTED_SUPERVISOR_INTERVAL_MILLIS = 5_000L;
    private static final long EXPECTED_SERVICE_WATCHDOG_INTERVAL_MILLIS =
            ServiceTransportWatchdog.TICK_INTERVAL_MILLIS;
    private static volatile long lastSupervisorTickElapsedMillis = -1L;
    private static volatile long nextSupervisorRetryElapsedMillis = -1L;
    private static volatile String supervisorReason = "not_started";
    private static long lastSupervisorPersistElapsedMillis = -1L;
    private static long lastPersistedRetryElapsedMillis = Long.MIN_VALUE;
    private static String lastPersistedSupervisorReason = "";
    private static volatile long lastServiceWatchdogTickElapsedMillis = -1L;

    private CloudState() {
    }

    static String deviceId(Context context) {
        return preferences(context).getString(DEVICE_ID, "");
    }

    static String accountName(Context context) {
        return preferences(context).getString(ACCOUNT_NAME, "");
    }

    static boolean isBound(Context context) {
        return !deviceId(context).isEmpty();
    }

    static boolean isRevoked(Context context) {
        return REVOKED.equals(preferences(context).getString(CONNECTION_STATE, ""));
    }

    static synchronized boolean setBinding(Context context, String accountName, String deviceId) {
        return setBindingInternal(context, accountName, deviceId, "");
    }

    static synchronized boolean setBindingPendingIdentityActivation(
            Context context,
            String accountName,
            String deviceId,
            String preparedAliasId
    ) {
        if (!("A".equals(preparedAliasId) || "B".equals(preparedAliasId))) {
            return false;
        }
        return setBindingInternal(context, accountName, deviceId, preparedAliasId);
    }

    private static boolean setBindingInternal(
            Context context,
            String accountName,
            String deviceId,
            String preparedAliasId
    ) {
        String normalizedAccount = safe(accountName, 80);
        String normalizedDevice = safe(deviceId, 80);
        SharedPreferences state = preferences(context);
        SharedPreferences.Editor editor = state.edit()
                .putString(ACCOUNT_NAME, normalizedAccount)
                .putString(DEVICE_ID, normalizedDevice)
                .putString(CONNECTION_STATE, BOUND_OFFLINE)
                .remove(LAST_ERROR_CODE)
                .remove(LAST_CONNECTED_AT)
                .remove(LAST_SERVER_ACTIVITY_AT)
                .remove(LAST_HEARTBEAT_ACK_AT)
                .putString(OUTBOX_STATE, "initializing")
                .putInt(OUTBOX_PENDING, 0)
                .remove(TASK_STORE_ERROR_CODE)
                .remove(TASK_STORE_LEGACY_BYTES)
                .remove(TASK_STORE_LEGACY_SHA256)
                .remove(TASK_RECOVERY_SAFE_AFTER)
                .remove(LAST_HEARTBEAT_SENT_AT)
                .remove(LAST_HEARTBEAT_SENT_EPOCH)
                .remove(LAST_HEARTBEAT_ACK_EPOCH)
                .remove(SERVER_SESSION_ID)
                .remove(LAST_SERVICE_WATCHDOG_TICK_AT)
                .remove(LAST_SERVICE_WATCHDOG_TICK_LAG)
                .remove(NEXT_RECONNECT_AT)
                .remove(RECONNECT_ATTEMPT)
                .remove(RECONNECT_REASON)
                .remove(SOCKET_EPOCH)
                .remove(LAST_RETIRE_REASON)
                .remove(LAST_RETIRE_AT)
                .remove(LAST_RETIRE_EPOCH)
                .putString(TRANSPORT_FAILURE_STAGE, "none")
                .putString(DEVICE_STATE_STATUS, "stale")
                .putString(DEVICE_STATE_FAILURE_STAGE, "none")
                .remove(DEVICE_STATE_CONFIRMED_AT)
                .putString(OUTBOX_FAILURE_STAGE, "none");
        if (preparedAliasId.isEmpty()) {
            editor.remove(PENDING_IDENTITY_ACTIVATION_ALIAS);
        } else {
            editor.putString(PENDING_IDENTITY_ACTIVATION_ALIAS, preparedAliasId);
        }
        boolean committed = editor.commit();
        boolean bindingPersisted = DurableBindingRules.isDurablyPersisted(
                committed,
                normalizedAccount,
                normalizedDevice,
                state.getString(ACCOUNT_NAME, ""),
                state.getString(DEVICE_ID, ""),
                state.getString(CONNECTION_STATE, "")
        );
        String persistedAlias = state.getString(PENDING_IDENTITY_ACTIVATION_ALIAS, "");
        return bindingPersisted && (
                preparedAliasId.isEmpty() ? persistedAlias.isEmpty() :
                        preparedAliasId.equals(persistedAlias)
        );
    }

    static boolean hasPendingIdentityActivation(Context context) {
        String aliasId = pendingIdentityActivationAlias(context);
        return "A".equals(aliasId) || "B".equals(aliasId);
    }

    static String pendingIdentityActivationAlias(Context context) {
        return preferences(context).getString(PENDING_IDENTITY_ACTIVATION_ALIAS, "");
    }

    static synchronized boolean clearPendingIdentityActivation(Context context) {
        SharedPreferences state = preferences(context);
        return state.edit().remove(PENDING_IDENTITY_ACTIVATION_ALIAS).commit() &&
                !state.contains(PENDING_IDENTITY_ACTIVATION_ALIAS);
    }

    static synchronized void clearBinding(Context context) {
        preferences(context).edit()
                .remove(ACCOUNT_NAME)
                .remove(DEVICE_ID)
                .putString(CONNECTION_STATE, UNBOUND)
                .remove(LAST_ERROR_CODE)
                .remove(LAST_CONNECTED_AT)
                .remove(LAST_SERVER_ACTIVITY_AT)
                .remove(LAST_HEARTBEAT_ACK_AT)
                .putString(OUTBOX_STATE, "not_bound")
                .putInt(OUTBOX_PENDING, 0)
                .remove(TASK_STORE_ERROR_CODE)
                .remove(TASK_STORE_LEGACY_BYTES)
                .remove(TASK_STORE_LEGACY_SHA256)
                .remove(TASK_RECOVERY_SAFE_AFTER)
                .remove(LAST_HEARTBEAT_SENT_AT)
                .remove(LAST_HEARTBEAT_SENT_EPOCH)
                .remove(LAST_HEARTBEAT_ACK_EPOCH)
                .remove(SERVER_SESSION_ID)
                .remove(LAST_SERVICE_WATCHDOG_TICK_AT)
                .remove(LAST_SERVICE_WATCHDOG_TICK_LAG)
                .remove(NEXT_RECONNECT_AT)
                .remove(RECONNECT_ATTEMPT)
                .remove(RECONNECT_REASON)
                .remove(SOCKET_EPOCH)
                .remove(LAST_RETIRE_REASON)
                .remove(LAST_RETIRE_AT)
                .remove(LAST_RETIRE_EPOCH)
                .putString(TRANSPORT_FAILURE_STAGE, "none")
                .putString(DEVICE_STATE_STATUS, "stale")
                .putString(DEVICE_STATE_FAILURE_STAGE, "none")
                .remove(DEVICE_STATE_CONFIRMED_AT)
                .putString(OUTBOX_FAILURE_STAGE, "none")
                .remove(PENDING_IDENTITY_ACTIVATION_ALIAS)
                .apply();
        DeviceStateCache.clear(context);
    }

    static synchronized void markRevoked(Context context) {
        preferences(context).edit()
                .putString(CONNECTION_STATE, REVOKED)
                .putString(LAST_ERROR_CODE, "device_revoked")
                .apply();
        ControlService.refreshNotification(context.getApplicationContext());
    }

    static String connectionState(Context context) {
        if (!CloudConfig.isConfigured()) {
            return CONFIG_REQUIRED;
        }
        String stored = preferences(context).getString(
                CONNECTION_STATE,
                isBound(context) ? BOUND_OFFLINE : UNBOUND
        );
        if ("ONLINE".equals(stored) || "OFFLINE".equals(stored) ||
                "DEGRADED".equals(stored)) {
            return isBound(context) ? BOUND_OFFLINE : UNBOUND;
        }
        return PersistedOnlineStateRules.effectiveState(
                stored,
                isBound(context),
                ServerActivityWatchdog.isFreshWallClock(
                        lastServerActivityAt(context),
                        System.currentTimeMillis(),
                        CloudChannelClient.SERVER_ACTIVITY_TIMEOUT_MILLIS));
    }

    static synchronized void setConnectionState(Context context, String state, String errorCode) {
        setConnectionState(context, state, errorCode, true);
    }

    static synchronized void setConnectionState(
            Context context,
            String state,
            String errorCode,
            boolean refreshNotification
    ) {
        if (isRevoked(context) && !REVOKED.equals(state) && !UNBOUND.equals(state)) {
            return;
        }
        SharedPreferences statePreferences = preferences(context);
        String previousState = statePreferences.getString(CONNECTION_STATE, "");
        SharedPreferences.Editor editor = statePreferences.edit()
                .putString(CONNECTION_STATE, safe(state, 32));
        if (ONLINE_FRESH.equals(state) &&
                !ONLINE_FRESH.equals(previousState) && !SUSPECT.equals(previousState)) {
            editor.putLong(LAST_CONNECTED_AT, System.currentTimeMillis());
        }
        if (errorCode == null || errorCode.trim().isEmpty()) {
            editor.remove(LAST_ERROR_CODE);
        } else {
            editor.putString(LAST_ERROR_CODE, safe(errorCode, 80));
        }
        editor.apply();
        if (refreshNotification) {
            ControlService.refreshNotification(context.getApplicationContext());
        }
    }

    static synchronized void recordConnectionError(Context context, String errorCode) {
        SharedPreferences.Editor editor = preferences(context).edit();
        if (errorCode == null || errorCode.trim().isEmpty()) {
            editor.remove(LAST_ERROR_CODE);
        } else {
            editor.putString(LAST_ERROR_CODE, safe(errorCode, 80));
        }
        editor.apply();
        ControlService.refreshNotification(context.getApplicationContext());
    }

    static synchronized void recordTransportFailureStage(Context context, String stage) {
        preferences(context).edit()
                .putString(TRANSPORT_FAILURE_STAGE, transportFailureStage(stage))
                .apply();
    }

    static String transportFailureStage(Context context) {
        return transportFailureStage(
                preferences(context).getString(TRANSPORT_FAILURE_STAGE, "none")
        );
    }

    static synchronized void recordDeviceStateFailure(Context context, String stage) {
        preferences(context).edit()
                .putString(DEVICE_STATE_STATUS, "stale")
                .putString(DEVICE_STATE_FAILURE_STAGE, deviceStateFailureStage(stage))
                .apply();
    }

    static synchronized void markDeviceStateStale(Context context) {
        preferences(context).edit()
                .putString(DEVICE_STATE_STATUS, "stale")
                .apply();
    }

    static synchronized void recordDeviceStateConfirmed(Context context, String state) {
        SharedPreferences.Editor editor = preferences(context).edit()
                .putString(DEVICE_STATE_STATUS, "fresh".equals(state) ? "fresh" : "stale");
        if ("fresh".equals(state)) {
            editor.putLong(DEVICE_STATE_CONFIRMED_AT, System.currentTimeMillis());
            editor.putString(DEVICE_STATE_FAILURE_STAGE, "none");
        }
        editor.apply();
    }

    static String deviceStateStatus(Context context) {
        return "fresh".equals(preferences(context).getString(DEVICE_STATE_STATUS, "stale"))
                ? "fresh" : "stale";
    }

    static String deviceStateFailureStage(Context context) {
        return deviceStateFailureStage(
                preferences(context).getString(DEVICE_STATE_FAILURE_STAGE, "none")
        );
    }

    static long deviceStateConfirmedAt(Context context) {
        return preferences(context).getLong(DEVICE_STATE_CONFIRMED_AT, 0L);
    }

    static synchronized void recordOutboxFailureStage(Context context, String stage) {
        preferences(context).edit()
                .putString(OUTBOX_FAILURE_STAGE, outboxFailureStage(stage))
                .apply();
    }

    static String outboxFailureStage(Context context) {
        return outboxFailureStage(
                preferences(context).getString(OUTBOX_FAILURE_STAGE, "none")
        );
    }

    static synchronized void markHeartbeatSent(Context context, long socketEpoch) {
        preferences(context).edit()
                .putLong(LAST_HEARTBEAT_SENT_AT, System.currentTimeMillis())
                .putLong(LAST_HEARTBEAT_SENT_EPOCH, socketEpoch)
                .apply();
    }

    static synchronized void markHeartbeatAcknowledged(Context context, long socketEpoch) {
        long now = System.currentTimeMillis();
        preferences(context).edit()
                .putLong(LAST_SERVER_ACTIVITY_AT, now)
                .putLong(LAST_HEARTBEAT_ACK_AT, now)
                .putLong(LAST_HEARTBEAT_ACK_EPOCH, socketEpoch)
                .apply();
    }

    static synchronized void recordServerSession(Context context, String sessionId) {
        preferences(context).edit()
                .putString(SERVER_SESSION_ID, safe(sessionId, 36))
                .apply();
    }

    static String serverSessionId(Context context) {
        return preferences(context).getString(SERVER_SESSION_ID, "unknown");
    }

    static synchronized void markServerActivity(Context context) {
        preferences(context).edit()
                .putLong(LAST_SERVER_ACTIVITY_AT, System.currentTimeMillis())
                .apply();
    }

    static String lastErrorCode(Context context) {
        return preferences(context).getString(LAST_ERROR_CODE, "");
    }

    static long lastConnectedAt(Context context) {
        return preferences(context).getLong(LAST_CONNECTED_AT, 0L);
    }

    static long lastHeartbeatAcknowledgedAt(Context context) {
        return preferences(context).getLong(LAST_HEARTBEAT_ACK_AT, 0L);
    }

    static long lastServerActivityAt(Context context) {
        return preferences(context).getLong(LAST_SERVER_ACTIVITY_AT, 0L);
    }

    static long lastHeartbeatSentAt(Context context) {
        return preferences(context).getLong(LAST_HEARTBEAT_SENT_AT, 0L);
    }

    static long lastHeartbeatSentEpoch(Context context) {
        return preferences(context).getLong(LAST_HEARTBEAT_SENT_EPOCH, -1L);
    }

    static long lastHeartbeatAckEpoch(Context context) {
        return preferences(context).getLong(LAST_HEARTBEAT_ACK_EPOCH, -1L);
    }

    static synchronized void setOutboxStatus(Context context, String state, int pending) {
        setTaskStoreStatus(context, state, pending, "", 0L, "");
    }

    static synchronized void setTaskStoreStatus(
            Context context,
            String state,
            int pending,
            String errorCode,
            long legacyBytes,
            String legacySha256
    ) {
        SharedPreferences.Editor editor = preferences(context).edit()
                .putString(OUTBOX_STATE, safe(state, 32))
                .putInt(OUTBOX_PENDING, Math.max(0, pending))
                .putLong(TASK_STORE_LEGACY_BYTES,
                        TaskStoreRules.normalizeDiagnosticByteSize(legacyBytes));
        if (errorCode == null || errorCode.trim().isEmpty()) {
            editor.remove(TASK_STORE_ERROR_CODE);
        } else {
            editor.putString(TASK_STORE_ERROR_CODE, safe(errorCode, 80));
        }
        if (legacySha256 == null || !legacySha256.matches("[a-f0-9]{64}")) {
            editor.remove(TASK_STORE_LEGACY_SHA256);
        } else {
            editor.putString(TASK_STORE_LEGACY_SHA256, legacySha256);
        }
        if (!TaskResultStore.RECOVERY_PENDING.equals(state)) {
            editor.remove(TASK_RECOVERY_SAFE_AFTER);
        }
        editor.apply();
    }

    static String outboxState(Context context) {
        return preferences(context).getString(OUTBOX_STATE, "unknown");
    }

    static int outboxPending(Context context) {
        return preferences(context).getInt(OUTBOX_PENDING, 0);
    }

    static String taskStoreErrorCode(Context context) {
        return preferences(context).getString(TASK_STORE_ERROR_CODE, "");
    }

    static long taskStoreLegacyBytes(Context context) {
        return preferences(context).getLong(TASK_STORE_LEGACY_BYTES, -1L);
    }

    static String taskStoreLegacySha256(Context context) {
        return preferences(context).getString(TASK_STORE_LEGACY_SHA256, "");
    }

    static synchronized void setTaskRecoverySafeAfter(Context context, long safeAfter) {
        SharedPreferences.Editor editor = preferences(context).edit();
        if (safeAfter <= 0L) {
            editor.remove(TASK_RECOVERY_SAFE_AFTER);
        } else {
            editor.putLong(TASK_RECOVERY_SAFE_AFTER, safeAfter);
        }
        editor.apply();
    }

    static long taskRecoverySafeAfter(Context context) {
        return preferences(context).getLong(TASK_RECOVERY_SAFE_AFTER, 0L);
    }

    static synchronized void setServiceState(Context context, String state) {
        preferences(context).edit()
                .putString(SERVICE_STATE, safe(state, 80))
                .apply();
    }

    static String serviceState(Context context) {
        return preferences(context).getString(SERVICE_STATE, "unknown");
    }

    static synchronized void recordReconnectResult(Context context, String code) {
        preferences(context).edit()
                .putString(LAST_RECONNECT_CODE, safe(code, 80))
                .putLong(LAST_RECONNECT_AT, System.currentTimeMillis())
                .apply();
    }

    static String lastReconnectCode(Context context) {
        return preferences(context).getString(LAST_RECONNECT_CODE, "never_requested");
    }

    static long lastReconnectAt(Context context) {
        return preferences(context).getLong(LAST_RECONNECT_AT, 0L);
    }

    static synchronized void recordServiceInstanceStarted(
            Context context,
            long serviceStartedAtWallMillis
    ) {
        SharedPreferences statePreferences = preferences(context);
        // Startup diagnostics only write service/process metadata. Transport lifecycle and
        // reconnect truth have one writer: the transport actor publisher.
        SharedPreferences.Editor editor = statePreferences.edit()
                .putString(PROCESS_INSTANCE_ID, PROCESS_INSTANCE_VALUE)
                .putLong(PROCESS_STARTED_AT, PROCESS_STARTED_AT_VALUE)
                .putInt(PROCESS_PID, android.os.Process.myPid())
                .putString(SERVICE_INSTANCE_ID, UUID.randomUUID().toString())
                .putLong(SERVICE_STARTED_AT, serviceStartedAtWallMillis);
        editor.apply();
    }

    static synchronized void recordProcessExitDiagnostic(Context context, String diagnostic) {
        preferences(context).edit()
                .putString(PREVIOUS_PROCESS_EXIT, safe(diagnostic, 800))
                .apply();
    }

    static String previousProcessExit(Context context) {
        return preferences(context).getString(PREVIOUS_PROCESS_EXIT, "{}");
    }

    static String processInstanceId(Context context) {
        return preferences(context).getString(PROCESS_INSTANCE_ID, "unknown");
    }

    static String currentProcessInstanceValue() {
        return PROCESS_INSTANCE_VALUE;
    }

    static long processStartedAt(Context context) {
        return preferences(context).getLong(PROCESS_STARTED_AT, 0L);
    }

    static int processPid(Context context) {
        return preferences(context).getInt(PROCESS_PID, -1);
    }

    static String serviceInstanceId(Context context) {
        return preferences(context).getString(SERVICE_INSTANCE_ID, "unknown");
    }

    static long serviceStartedAt(Context context) {
        return preferences(context).getLong(SERVICE_STARTED_AT, 0L);
    }

    static synchronized void recordServiceWatchdogTick(
            Context context,
            long elapsedRealtimeMillis
    ) {
        if (elapsedRealtimeMillis < 0L) {
            return;
        }
        long previous = lastServiceWatchdogTickElapsedMillis;
        long lag = previous < 0L || elapsedRealtimeMillis < previous
                ? 0L
                : Math.max(
                        0L,
                        elapsedRealtimeMillis - previous -
                                EXPECTED_SERVICE_WATCHDOG_INTERVAL_MILLIS
                );
        lastServiceWatchdogTickElapsedMillis = elapsedRealtimeMillis;
        SharedPreferences prefs = preferences(context);
        long maximumLag = Math.max(lag, prefs.getLong(MAX_SERVICE_WATCHDOG_LAG, 0L));
        prefs.edit()
                .putLong(LAST_SERVICE_WATCHDOG_TICK_AT, System.currentTimeMillis())
                .putLong(LAST_SERVICE_WATCHDOG_TICK_LAG, lag)
                .putLong(MAX_SERVICE_WATCHDOG_LAG, maximumLag)
                .apply();
    }

    static long serviceWatchdogLagMillis(Context context) {
        return preferences(context).getLong(LAST_SERVICE_WATCHDOG_TICK_LAG, 0L);
    }

    static long maxServiceWatchdogLagMillis(Context context) {
        return preferences(context).getLong(MAX_SERVICE_WATCHDOG_LAG, 0L);
    }

    static synchronized void recordRecoveryEpisode(
            Context context,
            RecoveryEpisode.Snapshot episode,
            long nowElapsedMillis,
            long wallNowMillis
    ) {
        SharedPreferences.Editor editor = preferences(context).edit()
                .putInt(RECOVERY_ATTEMPTS, Math.max(0, episode.attempts))
                .putString(RECOVERY_TRIGGER, safe(episode.trigger, 80))
                .putString(RECOVERY_FAILURE_STAGE, safe(episode.failureStage, 80))
                .putString(RECOVERY_OUTCOME, safe(episode.outcome, 32));
        long startAge = Math.max(0L, nowElapsedMillis - episode.startedElapsedMillis);
        editor.putLong(RECOVERY_STARTED_AT, Math.max(0L, wallNowMillis - startAge));
        if (episode.completedElapsedMillis >= 0L) {
            long completedAge = Math.max(0L, nowElapsedMillis - episode.completedElapsedMillis);
            editor.putLong(RECOVERY_COMPLETED_AT, Math.max(0L, wallNowMillis - completedAge));
        } else {
            editor.remove(RECOVERY_COMPLETED_AT);
        }
        editor.apply();
    }

    static long recoveryStartedAt(Context context) {
        return preferences(context).getLong(RECOVERY_STARTED_AT, 0L);
    }

    static long recoveryCompletedAt(Context context) {
        return preferences(context).getLong(RECOVERY_COMPLETED_AT, 0L);
    }

    static int recoveryAttempts(Context context) {
        return preferences(context).getInt(RECOVERY_ATTEMPTS, 0);
    }

    static String recoveryTrigger(Context context) {
        return preferences(context).getString(RECOVERY_TRIGGER, "never");
    }

    static String recoveryFailureStage(Context context) {
        return preferences(context).getString(RECOVERY_FAILURE_STAGE, "none");
    }

    static String recoveryOutcome(Context context) {
        return preferences(context).getString(RECOVERY_OUTCOME, "never");
    }

    static synchronized void recordReconnectPlan(
            Context context,
            long nowElapsedMillis,
            CloudConnectionLifecycle.Snapshot snapshot,
            long socketEpoch
    ) {
        SharedPreferences.Editor editor = preferences(context).edit()
                .putInt(RECONNECT_ATTEMPT, Math.max(0, snapshot.reconnectAttempt))
                .putString(RECONNECT_REASON, safe(snapshot.reason, 80))
                .putLong(SOCKET_EPOCH, socketEpoch);
        if (snapshot.retryAtElapsedMillis < 0L || nowElapsedMillis < 0L) {
            editor.remove(NEXT_RECONNECT_AT);
        } else {
            long remaining = Math.max(0L, snapshot.retryAtElapsedMillis - nowElapsedMillis);
            editor.putLong(NEXT_RECONNECT_AT, System.currentTimeMillis() + remaining);
        }
        editor.apply();
    }

    static synchronized void recordSocketEpoch(Context context, long socketEpoch) {
        preferences(context).edit().putLong(SOCKET_EPOCH, socketEpoch).apply();
    }

    static synchronized void recordSocketRetirement(
            Context context,
            String reason,
            long socketEpoch
    ) {
        preferences(context).edit()
                .putString(LAST_RETIRE_REASON, safe(reason, 80))
                .putLong(LAST_RETIRE_AT, System.currentTimeMillis())
                .putLong(LAST_RETIRE_EPOCH, socketEpoch)
                .apply();
    }

    static int reconnectAttempt(Context context) {
        return preferences(context).getInt(RECONNECT_ATTEMPT, 0);
    }

    static long nextReconnectAt(Context context) {
        return preferences(context).getLong(NEXT_RECONNECT_AT, 0L);
    }

    static String reconnectReason(Context context) {
        return preferences(context).getString(RECONNECT_REASON, "not_started");
    }

    static long socketEpoch(Context context) {
        return preferences(context).getLong(SOCKET_EPOCH, -1L);
    }

    static String lastRetireReason(Context context) {
        return preferences(context).getString(LAST_RETIRE_REASON, "never");
    }

    static long lastRetireAt(Context context) {
        return preferences(context).getLong(LAST_RETIRE_AT, 0L);
    }

    static long lastRetireEpoch(Context context) {
        return preferences(context).getLong(LAST_RETIRE_EPOCH, -1L);
    }

    static synchronized void recordSupervisorTick(
            Context context,
            long elapsedRealtimeMillis,
            long nextRetryElapsedMillis,
            String reason
    ) {
        if (elapsedRealtimeMillis < 0L) {
            return;
        }
        long previousTick = lastSupervisorTickElapsedMillis;
        long tickLag = previousTick < 0L || elapsedRealtimeMillis < previousTick
                ? -1L
                : Math.max(
                        0L,
                        elapsedRealtimeMillis - previousTick -
                                EXPECTED_SUPERVISOR_INTERVAL_MILLIS
                );
        String normalizedReason = safe(reason, 80);
        lastSupervisorTickElapsedMillis = elapsedRealtimeMillis;
        nextSupervisorRetryElapsedMillis = nextRetryElapsedMillis;
        supervisorReason = normalizedReason;
        boolean persist = lastSupervisorPersistElapsedMillis < 0L ||
                elapsedRealtimeMillis - lastSupervisorPersistElapsedMillis >= 30_000L ||
                !normalizedReason.equals(lastPersistedSupervisorReason) ||
                nextRetryElapsedMillis != lastPersistedRetryElapsedMillis ||
                tickLag >= 5_000L;
        if (!persist) {
            return;
        }
        lastSupervisorPersistElapsedMillis = elapsedRealtimeMillis;
        lastPersistedRetryElapsedMillis = nextRetryElapsedMillis;
        lastPersistedSupervisorReason = normalizedReason;
        SharedPreferences prefs = preferences(context);
        SharedPreferences.Editor editor = prefs.edit()
                .putLong(LAST_SUPERVISOR_TICK_AT, System.currentTimeMillis())
                .putString(SUPERVISOR_REASON, normalizedReason);
        if (tickLag < 0L) {
            editor.remove(LAST_SUPERVISOR_TICK_LAG);
        } else {
            editor.putLong(LAST_SUPERVISOR_TICK_LAG, tickLag);
            editor.putLong(
                    MAX_SUPERVISOR_LAG,
                    Math.max(tickLag, prefs.getLong(MAX_SUPERVISOR_LAG, 0L)));
        }
        if (nextRetryElapsedMillis < 0L) {
            editor.remove(NEXT_RECONNECT_AT);
        } else {
            editor.putLong(
                    NEXT_RECONNECT_AT,
                    System.currentTimeMillis() +
                            Math.max(0L, nextRetryElapsedMillis - elapsedRealtimeMillis)
            );
        }
        editor.apply();
    }

    static long supervisorTickAgeMillis(Context context, long elapsedRealtimeMillis) {
        long tick = lastSupervisorTickElapsedMillis;
        if (tick >= 0L && elapsedRealtimeMillis >= tick) {
            return elapsedRealtimeMillis - tick;
        }
        long wallTick = preferences(context).getLong(LAST_SUPERVISOR_TICK_AT, 0L);
        long now = System.currentTimeMillis();
        return wallTick <= 0L || now < wallTick ? -1L : now - wallTick;
    }

    static long supervisorNextRetryInMillis(Context context, long elapsedRealtimeMillis) {
        long retryAt = nextSupervisorRetryElapsedMillis;
        if (retryAt >= 0L) {
            return Math.max(0L, retryAt - elapsedRealtimeMillis);
        }
        long wallRetryAt = preferences(context).getLong(NEXT_RECONNECT_AT, 0L);
        return wallRetryAt <= 0L ? -1L : Math.max(0L, wallRetryAt - System.currentTimeMillis());
    }

    static long supervisorTickLagMillis(Context context) {
        return preferences(context).getLong(LAST_SUPERVISOR_TICK_LAG, -1L);
    }

    static long maxSupervisorTickLagMillis(Context context) {
        return preferences(context).getLong(MAX_SUPERVISOR_LAG, 0L);
    }

    static String supervisorReason(Context context) {
        if (!"not_started".equals(supervisorReason)) {
            return supervisorReason;
        }
        return preferences(context).getString(SUPERVISOR_REASON, "not_started");
    }

    private static SharedPreferences preferences(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String transportFailureStage(String value) {
        if ("heartbeat_scheduler".equals(value) ||
                "heartbeat_send_rejected".equals(value) ||
                "websocket_exception".equals(value)) {
            return value;
        }
        return "none";
    }

    private static String deviceStateFailureStage(String value) {
        if ("device_snapshot_collect".equals(value) ||
                "device_snapshot_normalize".equals(value) ||
                "device_snapshot_serialize".equals(value) ||
                "device_snapshot_send".equals(value) ||
                "device_state_cache_store".equals(value)) {
            return value;
        }
        return "none";
    }

    private static String outboxFailureStage(String value) {
        if ("outbox_load".equals(value) || "outbox_replay".equals(value) ||
                "outbox_send".equals(value)) {
            return value;
        }
        return "none";
    }

    private static String safe(String value, int maximum) {
        String normalized = value == null ? "" : value.trim();
        return normalized.length() <= maximum ? normalized : normalized.substring(0, maximum);
    }
}
