package com.feifei.phonebridge;

import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** Last-value publication lane; its sink never runs on the caller/transport thread. */
final class CoalescingPublicationLane<T> {
    interface Sink<T> {
        void publish(T value);
    }

    private final Executor executor;
    private final Sink<T> sink;
    private final AtomicReference<T> pending = new AtomicReference<>();
    private final AtomicBoolean drainScheduled = new AtomicBoolean(false);

    CoalescingPublicationLane(Executor executor, Sink<T> sink) {
        this.executor = executor;
        this.sink = sink;
    }

    void submit(T value) {
        if (value == null) {
            return;
        }
        pending.set(value);
        scheduleDrain();
    }

    private void scheduleDrain() {
        if (!drainScheduled.compareAndSet(false, true)) {
            return;
        }
        try {
            executor.execute(this::drain);
        } catch (RejectedExecutionException stopped) {
            drainScheduled.set(false);
        }
    }

    private void drain() {
        try {
            T value;
            while ((value = pending.getAndSet(null)) != null) {
                try {
                    sink.publish(value);
                } catch (Throwable ignored) {
                    // Publication diagnostics may degrade; they never terminate the lane.
                }
            }
        } finally {
            drainScheduled.set(false);
            if (pending.get() != null) {
                scheduleDrain();
            }
        }
    }
}
