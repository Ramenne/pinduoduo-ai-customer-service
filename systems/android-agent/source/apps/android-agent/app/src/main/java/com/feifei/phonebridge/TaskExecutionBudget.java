package com.feifei.phonebridge;

/** Keeps side-effect verification inside the server-issued task lifetime. */
final class TaskExecutionBudget {
    private static final long RESULT_DELIVERY_MARGIN_SECONDS = 2L;

    private TaskExecutionBudget() {
    }

    static long verificationMillis(long expiresAtSeconds, long nowSeconds) {
        long remainingSeconds = expiresAtSeconds - nowSeconds;
        if (remainingSeconds <= RESULT_DELIVERY_MARGIN_SECONDS) {
            return 0L;
        }
        long verificationSeconds = remainingSeconds - RESULT_DELIVERY_MARGIN_SECONDS;
        if (verificationSeconds > Long.MAX_VALUE / 1_000L) {
            return Long.MAX_VALUE;
        }
        return verificationSeconds * 1_000L;
    }
}
