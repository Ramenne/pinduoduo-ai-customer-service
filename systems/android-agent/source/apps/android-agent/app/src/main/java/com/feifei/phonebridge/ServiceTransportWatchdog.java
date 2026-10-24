package com.feifei.phonebridge;

/**
 * Independent service-owned rescue cadence for the realtime transport.
 *
 * The normal heartbeat and channel supervisor have their own executors.  This
 * small monotonic gate is driven by the foreground Service main looper so one
 * stalled executor cannot leave an otherwise live Service permanently silent.
 */
final class ServiceTransportWatchdog {
    static final long TICK_INTERVAL_MILLIS = 15_000L;
    static final long HEARTBEAT_RESCUE_AFTER_MILLIS = 35_000L;

    private long lastHeartbeatAttemptElapsedMillis = -1L;

    synchronized void recordHeartbeatAttempt(long elapsedRealtimeMillis) {
        if (elapsedRealtimeMillis >= 0L) {
            lastHeartbeatAttemptElapsedMillis = elapsedRealtimeMillis;
        }
    }

    synchronized boolean claimHeartbeatRescue(
            long elapsedRealtimeMillis,
            boolean authenticated
    ) {
        if (!authenticated || elapsedRealtimeMillis < 0L) {
            return false;
        }
        if (lastHeartbeatAttemptElapsedMillis >= 0L &&
                elapsedRealtimeMillis >= lastHeartbeatAttemptElapsedMillis &&
                elapsedRealtimeMillis - lastHeartbeatAttemptElapsedMillis <
                        HEARTBEAT_RESCUE_AFTER_MILLIS) {
            return false;
        }
        lastHeartbeatAttemptElapsedMillis = elapsedRealtimeMillis;
        return true;
    }
}
