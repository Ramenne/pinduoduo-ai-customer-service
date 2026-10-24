package com.feifei.phonebridge;

/** Server epoch time anchored to Android's monotonic elapsed-realtime clock. */
final class TrustedServerClock {
    private boolean anchored;
    private long serverBaseSeconds;
    private long elapsedBaseMillis;

    synchronized void anchor(long serverSeconds, long elapsedRealtimeMillis) {
        if (serverSeconds <= 0L || elapsedRealtimeMillis < 0L) {
            throw new IllegalArgumentException("invalid_server_clock_anchor");
        }
        serverBaseSeconds = serverSeconds;
        elapsedBaseMillis = elapsedRealtimeMillis;
        anchored = true;
    }

    synchronized long nowSeconds(long elapsedRealtimeMillis, long fallbackWallSeconds) {
        if (!anchored) {
            return fallbackWallSeconds;
        }
        long elapsedDelta = Math.max(0L, elapsedRealtimeMillis - elapsedBaseMillis);
        long secondsDelta = elapsedDelta / 1000L;
        if (secondsDelta > Long.MAX_VALUE - serverBaseSeconds) {
            return Long.MAX_VALUE;
        }
        return serverBaseSeconds + secondsDelta;
    }
}
