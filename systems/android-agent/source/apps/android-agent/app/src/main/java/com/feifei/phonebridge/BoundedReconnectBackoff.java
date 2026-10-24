package com.feifei.phonebridge;

final class BoundedReconnectBackoff {
    static final long NO_DEADLINE = -1L;

    private final long maximumDelayMillis;
    private int attempt;
    private long retryAtElapsedMillis = NO_DEADLINE;

    BoundedReconnectBackoff(long maximumDelayMillis) {
        if (maximumDelayMillis <= 0L) {
            throw new IllegalArgumentException("maximum_delay_must_be_positive");
        }
        this.maximumDelayMillis = maximumDelayMillis;
    }

    synchronized long schedule(long nowElapsedMillis, long jitterMillis) {
        if (retryAtElapsedMillis > nowElapsedMillis) {
            return retryAtElapsedMillis;
        }
        long exponentialMillis = 1000L << Math.min(attempt, 6);
        attempt = Math.min(attempt + 1, 16);
        long delay = Math.min(maximumDelayMillis,
                exponentialMillis + Math.max(0L, jitterMillis));
        retryAtElapsedMillis = nowElapsedMillis + delay;
        return retryAtElapsedMillis;
    }

    synchronized void retryNow(long nowElapsedMillis) {
        retryAtElapsedMillis = nowElapsedMillis;
    }

    synchronized boolean claimIfDue(long nowElapsedMillis) {
        if (retryAtElapsedMillis == NO_DEADLINE || nowElapsedMillis < retryAtElapsedMillis) {
            return false;
        }
        retryAtElapsedMillis = NO_DEADLINE;
        return true;
    }

    synchronized long retryAtElapsedMillis() {
        return retryAtElapsedMillis;
    }

    synchronized void authenticated() {
        attempt = 0;
        retryAtElapsedMillis = NO_DEADLINE;
    }

    synchronized void clear() {
        retryAtElapsedMillis = NO_DEADLINE;
    }
}
