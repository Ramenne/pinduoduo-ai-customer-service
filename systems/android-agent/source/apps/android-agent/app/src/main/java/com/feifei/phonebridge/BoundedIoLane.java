package com.feifei.phonebridge;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** Single-purpose bounded I/O lane that never executes work on its caller. */
final class BoundedIoLane {
    private final ThreadPoolExecutor executor;

    BoundedIoLane(String threadName, int maximumQueuedWork) {
        executor = new ThreadPoolExecutor(
                1,
                1,
                0L,
                TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(maximumQueuedWork),
                runnable -> {
                    Thread thread = new Thread(runnable, threadName);
                    thread.setDaemon(true);
                    return thread;
                },
                new ThreadPoolExecutor.AbortPolicy());
    }

    boolean submit(Runnable work) {
        try {
            executor.execute(work);
            return true;
        } catch (RejectedExecutionException stoppedOrFull) {
            return false;
        }
    }

    boolean runAndWait(Runnable work, long timeoutMillis) {
        CountDownLatch done = new CountDownLatch(1);
        if (!submit(() -> {
            try {
                work.run();
            } finally {
                done.countDown();
            }
        })) {
            return false;
        }
        try {
            return done.await(timeoutMillis, TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    void stopNow() {
        executor.shutdownNow();
    }

    /** Finish already accepted work without accepting another item. */
    void stopAfterCurrent() {
        executor.shutdown();
    }

    boolean awaitStopped(long timeoutMillis) {
        try {
            return executor.awaitTermination(timeoutMillis, TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    boolean isStopped() {
        return executor.isTerminated();
    }
}
