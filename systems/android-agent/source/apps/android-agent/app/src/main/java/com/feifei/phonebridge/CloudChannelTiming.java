package com.feifei.phonebridge;

/** Shared, Android-free timing contract for the Board 0 channel lifecycle. */
final class CloudChannelTiming {
    // Client view: time since authenticated server activity/ACK on this socket.
    static final long SUSPECT_AFTER_MILLIS = 30_000L;
    static final long RETIRE_AFTER_MILLIS = 32_000L;
    // End-to-end from the last authenticated server activity, including half-open detection.
    static final long ORDINARY_AVAILABILITY_BUDGET_MILLIS = 55_000L;
    // When Android/OkHttp explicitly reports a disconnect, detection has already completed.
    static final long OBSERVED_DISCONNECT_RECOVERY_BUDGET_MILLIS = 40_000L;
    static final long MAX_RECONNECT_BACKOFF_MILLIS = 4_000L;
    static final long CONNECT_TIMEOUT_MILLIS = 8_000L;
    static final long AUTHENTICATION_TIMEOUT_MILLIS = 12_000L;
    // Server view: time since any client-to-server inbound websocket message.
    // This is the opposite liveness direction and is not a margin for RETIRE_AFTER_MILLIS.
    static final long SERVER_CLIENT_INBOUND_SILENCE_TIMEOUT_MILLIS = 90_000L;

    static {
        if (SUSPECT_AFTER_MILLIS <= 0L ||
                SUSPECT_AFTER_MILLIS >= RETIRE_AFTER_MILLIS ||
                RETIRE_AFTER_MILLIS >= ORDINARY_AVAILABILITY_BUDGET_MILLIS ||
                MAX_RECONNECT_BACKOFF_MILLIS >= OBSERVED_DISCONNECT_RECOVERY_BUDGET_MILLIS ||
                SERVER_CLIENT_INBOUND_SILENCE_TIMEOUT_MILLIS <= 0L) {
            throw new IllegalStateException("invalid_cloud_liveness_contract");
        }
    }

    private CloudChannelTiming() {
    }
}
