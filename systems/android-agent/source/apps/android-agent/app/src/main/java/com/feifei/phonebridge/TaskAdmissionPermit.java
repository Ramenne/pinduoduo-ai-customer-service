package com.feifei.phonebridge;

import java.util.concurrent.atomic.AtomicBoolean;

/** Cross-lane fence preventing an ACK/execution after the server delivery lease has expired. */
final class TaskAdmissionPermit {
    private final AtomicBoolean valid = new AtomicBoolean(true);
    private final long deadlineNanos;

    TaskAdmissionPermit(long timeoutMillis) {
        deadlineNanos = System.nanoTime() + timeoutMillis * 1_000_000L;
    }

    void expire() {
        valid.set(false);
    }

    boolean isValid() {
        return valid.get() && System.nanoTime() < deadlineNanos;
    }
}
