package com.feifei.phonebridge;

/** Keeps one failing scheduled iteration from cancelling every later period. */
final class SafeScheduledTask {
    private SafeScheduledTask() {
    }

    static void run(Runnable action, Runnable failureHandler) {
        try {
            action.run();
        } catch (Throwable failure) {
            try {
                failureHandler.run();
            } catch (Throwable ignored) {
                // A diagnostic write must never escape into ScheduledExecutorService.
            }
        }
    }
}
