package com.feifei.phonebridge;

public final class ForegroundLaunchVerifierSelfTest {
    private ForegroundLaunchVerifierSelfTest() {
    }

    public static void main(String[] args) throws Exception {
        delayedUserConfirmationIsObserved();
        shortTaskBudgetTimesOutWithoutUsingGlobalMaximum();
        alreadyForegroundSucceedsWithNoRemainingBudget();
        boundedWindowNeverExceedsThirtySeconds();
        taskExpiryKeepsResultDeliveryMargin();
        System.out.println("FOREGROUND_LAUNCH_VERIFIER_SELF_TEST_PASS");
    }

    private static void delayedUserConfirmationIsObserved() throws Exception {
        FakeTime time = new FakeTime();
        boolean observed = ForegroundLaunchVerifier.await(
                "com.taobao.idlefish",
                58_000L,
                () -> time.nowMillis() >= 4_500L
                        ? "com.taobao.idlefish"
                        : "com.android.systemui",
                time,
                time
        );
        require(observed, "confirmation after 4.5s must be observed");
        require(time.nowMillis() == 4_500L, "polling should stop at first observation");
    }

    private static void shortTaskBudgetTimesOutWithoutUsingGlobalMaximum() throws Exception {
        FakeTime time = new FakeTime();
        boolean observed = ForegroundLaunchVerifier.await(
                "com.taobao.idlefish",
                2_000L,
                () -> "com.android.systemui",
                time,
                time
        );
        require(!observed, "unobserved launch must time out");
        require(time.nowMillis() == 2_000L, "task budget must bound verification");
    }

    private static void alreadyForegroundSucceedsWithNoRemainingBudget() throws Exception {
        FakeTime time = new FakeTime();
        boolean observed = ForegroundLaunchVerifier.await(
                "com.taobao.idlefish",
                0L,
                () -> "com.taobao.idlefish",
                time,
                time
        );
        require(observed, "current foreground must be checked before timeout");
        require(time.nowMillis() == 0L, "already foreground must not sleep");
    }

    private static void boundedWindowNeverExceedsThirtySeconds() {
        require(
                ForegroundLaunchVerifier.boundedWaitMillis(Long.MAX_VALUE) == 30_000L,
                "global confirmation window must remain bounded"
        );
        require(
                ForegroundLaunchVerifier.boundedWaitMillis(-1L) == 0L,
                "negative task budget must fail closed"
        );
    }

    private static void taskExpiryKeepsResultDeliveryMargin() {
        require(
                TaskExecutionBudget.verificationMillis(100L, 90L) == 8_000L,
                "two seconds must remain for durable result delivery"
        );
        require(
                TaskExecutionBudget.verificationMillis(100L, 98L) == 0L,
                "launch must not start without verification budget"
        );
        require(
                TaskExecutionBudget.verificationMillis(100L, 101L) == 0L,
                "expired task budget must fail closed"
        );
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class FakeTime
            implements ForegroundLaunchVerifier.MonotonicClock,
            ForegroundLaunchVerifier.Sleeper {
        private long nowMillis;

        @Override
        public long nowMillis() {
            return nowMillis;
        }

        @Override
        public void sleep(long millis) {
            if (millis <= 0L) {
                throw new AssertionError("sleep must be positive");
            }
            nowMillis += millis;
        }
    }
}
