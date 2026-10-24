package com.feifei.phonebridge;

import java.util.function.Supplier;

/** Bounded foreground verification for an app launch that may require user confirmation. */
final class ForegroundLaunchVerifier {
    static final long MAX_CONFIRMATION_WAIT_MILLIS = 30_000L;
    static final long POLL_MILLIS = 150L;

    interface MonotonicClock {
        long nowMillis();
    }

    interface Sleeper {
        void sleep(long millis) throws InterruptedException;
    }

    private ForegroundLaunchVerifier() {
    }

    static long boundedWaitMillis(long remainingTaskBudgetMillis) {
        if (remainingTaskBudgetMillis <= 0L) {
            return 0L;
        }
        return Math.min(MAX_CONFIRMATION_WAIT_MILLIS, remainingTaskBudgetMillis);
    }

    static boolean await(
            String expectedPackage,
            long remainingTaskBudgetMillis,
            Supplier<String> activePackage,
            MonotonicClock clock,
            Sleeper sleeper
    ) throws InterruptedException {
        long timeoutMillis = boundedWaitMillis(remainingTaskBudgetMillis);
        long startedAt = clock.nowMillis();
        long deadline = saturatedAdd(startedAt, timeoutMillis);
        while (true) {
            if (expectedPackage.equals(activePackage.get())) {
                return true;
            }
            long now = clock.nowMillis();
            if (now >= deadline) {
                return false;
            }
            sleeper.sleep(Math.min(POLL_MILLIS, deadline - now));
        }
    }

    private static long saturatedAdd(long left, long right) {
        if (right > 0L && left > Long.MAX_VALUE - right) {
            return Long.MAX_VALUE;
        }
        return left + right;
    }
}
