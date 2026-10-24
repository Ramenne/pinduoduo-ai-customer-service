package com.feifei.phonebridge;

import android.content.Context;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;

/**
 * Non-transport lane for durable diagnostics and notification refreshes.
 *
 * <p>Publishing is last-value-coalesced. A slow SharedPreferences filesystem or system-server
 * notification Binder must never hold the transport actor or suppress heartbeat progression.</p>
 */
final class TransportStatePublisher {
    interface Publication {
        void publish(Context context);
    }

    private final Context context;
    private final ExecutorService executor;
    private final CoalescingPublicationLane<Publication> lane;

    TransportStatePublisher(Context context) {
        this.context = context.getApplicationContext();
        this.executor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "oin-transport-publisher");
            thread.setDaemon(true);
            return thread;
        });
        this.lane = new CoalescingPublicationLane<>(executor, publication -> {
            try {
                publication.publish(this.context);
            } catch (Throwable ignored) {
                // Last-value diagnostic publication is best effort; transport is authoritative.
            }
        });
    }

    void submit(Publication publication) {
        if (publication == null) {
            return;
        }
        lane.submit(publication);
    }

    void execute(Runnable work) {
        try {
            executor.execute(() -> {
                try {
                    work.run();
                } catch (Throwable ignored) {
                    // Diagnostics cannot retire or block a healthy transport.
                }
            });
        } catch (RejectedExecutionException ignored) {
        }
    }

    boolean executeAndWait(Runnable work, long timeoutMillis) {
        CountDownLatch done = new CountDownLatch(1);
        try {
            executor.execute(() -> {
                try {
                    work.run();
                } finally {
                    done.countDown();
                }
            });
        } catch (RejectedExecutionException stopped) {
            return false;
        }
        try {
            return done.await(timeoutMillis, TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    void stop() {
        executor.shutdownNow();
    }
}
