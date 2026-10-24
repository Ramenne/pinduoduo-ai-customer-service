package com.feifei.phonebridge;

final class ConnectionAttemptGate {
    private static final long NONE = Long.MIN_VALUE;
    private long activeEpoch = NONE;

    synchronized boolean tryStart(long epoch) {
        if (activeEpoch != NONE) {
            return false;
        }
        activeEpoch = epoch;
        return true;
    }

    synchronized boolean finish(long epoch) {
        if (activeEpoch != epoch) {
            return false;
        }
        activeEpoch = NONE;
        return true;
    }

    synchronized boolean isActive() {
        return activeEpoch != NONE;
    }

    synchronized void clear() {
        activeEpoch = NONE;
    }
}
