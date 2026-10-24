package com.feifei.phonebridge;

/** Bounded policy for failures that happen before the guardian acquires the lease. */
final class RecoveryGuardianRetryPolicy {
    static final int MAX_PRECLAIM_RETRIES = 2;

    private RecoveryGuardianRetryPolicy() {
    }

    static boolean mayRetry(int completedRetries) {
        return completedRetries >= 0 && completedRetries < MAX_PRECLAIM_RETRIES;
    }

    static long delayMillis(int completedRetries) {
        if (!mayRetry(completedRetries)) {
            return -1L;
        }
        return 2_000L << completedRetries;
    }
}
