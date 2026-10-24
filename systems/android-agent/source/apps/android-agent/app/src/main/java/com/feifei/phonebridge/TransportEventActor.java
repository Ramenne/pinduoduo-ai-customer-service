package com.feifei.phonebridge;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Single authority for realtime transport state transitions.
 *
 * <p>OkHttp, Android lifecycle and network callbacks only enqueue events. Socket ownership,
 * epochs, heartbeat admission and reconnect transitions are mutated on this actor thread. The
 * actor never owns persistence, notification, SQLite or accessibility work.</p>
 */
final class TransportEventActor {
    private final ScheduledThreadPoolExecutor executor;
    private volatile Thread actorThread;

    TransportEventActor(String threadName) {
        executor = new ScheduledThreadPoolExecutor(1, runnable -> {
            Thread thread = new Thread(runnable, threadName);
            thread.setDaemon(true);
            actorThread = thread;
            return thread;
        });
        executor.setRemoveOnCancelPolicy(true);
        executor.setExecuteExistingDelayedTasksAfterShutdownPolicy(false);
        executor.setContinueExistingPeriodicTasksAfterShutdownPolicy(false);
    }

    void post(Runnable event) {
        executor.execute(event);
    }

    ScheduledFuture<?> schedule(Runnable event, long delay, TimeUnit unit) {
        return executor.schedule(event, delay, unit);
    }

    ScheduledFuture<?> scheduleWithFixedDelay(
            Runnable event,
            long initialDelay,
            long delay,
            TimeUnit unit
    ) {
        return executor.scheduleWithFixedDelay(event, initialDelay, delay, unit);
    }

    boolean isActorThread() {
        return Thread.currentThread() == actorThread;
    }

    void requireActorThread() {
        if (!isActorThread()) {
            throw new IllegalStateException("transport_state_outside_actor");
        }
    }

    void postAndWait(Runnable event, long timeoutMillis) {
        if (isActorThread()) {
            event.run();
            return;
        }
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        post(() -> {
            try {
                event.run();
            } catch (Throwable thrown) {
                failure.set(thrown);
            } finally {
                done.countDown();
            }
        });
        try {
            if (!done.await(timeoutMillis, TimeUnit.MILLISECONDS)) {
                throw new IllegalStateException("transport_actor_stop_timeout");
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("transport_actor_stop_interrupted", interrupted);
        }
        Throwable thrown = failure.get();
        if (thrown instanceof RuntimeException) {
            throw (RuntimeException) thrown;
        }
        if (thrown != null) {
            throw new IllegalStateException("transport_actor_event_failed", thrown);
        }
    }

    void shutdownNow() {
        executor.shutdownNow();
    }

    boolean isShutdown() {
        return executor.isShutdown();
    }

    ScheduledExecutorService schedulerForTests() {
        return executor;
    }
}
