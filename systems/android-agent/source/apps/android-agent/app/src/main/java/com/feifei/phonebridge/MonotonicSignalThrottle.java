package com.feifei.phonebridge;

/** Bounded monotonic admission for high-frequency system callbacks. */
final class MonotonicSignalThrottle {
    private final long minimumIntervalMillis;
    private long lastAcceptedAt = Long.MIN_VALUE;

    MonotonicSignalThrottle(long minimumIntervalMillis) {
        if (minimumIntervalMillis < 0L) {
            throw new IllegalArgumentException("invalid_minimum_interval");
        }
        this.minimumIntervalMillis = minimumIntervalMillis;
    }

    synchronized boolean tryAcquire(long nowElapsedMillis) {
        if (nowElapsedMillis < 0L) {
            return false;
        }
        if (lastAcceptedAt != Long.MIN_VALUE && nowElapsedMillis >= lastAcceptedAt &&
                nowElapsedMillis - lastAcceptedAt < minimumIntervalMillis) {
            return false;
        }
        lastAcceptedAt = nowElapsedMillis;
        return true;
    }
}
