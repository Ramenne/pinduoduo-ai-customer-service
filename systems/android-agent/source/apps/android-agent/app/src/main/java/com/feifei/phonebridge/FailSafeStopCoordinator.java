package com.feifei.phonebridge;

/** Runs every cleanup step even when an earlier step times out or throws. */
final class FailSafeStopCoordinator {
    private int failures;

    void run(Runnable step) {
        try {
            step.run();
        } catch (Throwable ignored) {
            failures++;
        }
    }

    int failures() {
        return failures;
    }
}
