package com.feifei.phonebridge.plugin.xianyu;

/** Bounded retry decision for one read-only Xianyu accessibility snapshot. */
final class XianyuSnapshotRetryPolicy {
    static final long STABILIZATION_DELAY_MILLIS = 1000L;
    static final long MIN_RETRY_BUDGET_MILLIS = 3000L;

    private XianyuSnapshotRetryPolicy() {
    }

    static boolean shouldRetry(boolean firstIsXianyu, boolean firstTruncated,
                               long remainingTaskBudgetMillis) {
        return firstIsXianyu && firstTruncated
                && remainingTaskBudgetMillis >= MIN_RETRY_BUDGET_MILLIS;
    }

    static boolean shouldUseSecond(String firstPage, int firstNodeCount,
                                   boolean secondIsXianyu, String secondPage,
                                   int secondNodeCount, boolean secondTruncated) {
        if (!secondIsXianyu || firstPage == null || !firstPage.equals(secondPage)) {
            return false;
        }
        return !secondTruncated || secondNodeCount >= firstNodeCount;
    }
}
