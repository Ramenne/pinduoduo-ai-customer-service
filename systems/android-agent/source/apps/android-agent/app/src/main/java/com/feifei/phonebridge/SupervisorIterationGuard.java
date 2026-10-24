package com.feifei.phonebridge;

final class SupervisorIterationGuard {
    private SupervisorIterationGuard() {
    }

    static void run(Runnable iteration, Runnable runtimeFailureHandler) {
        try {
            iteration.run();
        } catch (RuntimeException failure) {
            try {
                runtimeFailureHandler.run();
            } catch (RuntimeException ignored) {
                // A diagnostic failure must not escape and cancel the fixed-delay supervisor.
            }
        }
    }
}
