package com.feifei.phonebridge;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public final class ChannelSeparationSelfTest {
    private ChannelSeparationSelfTest() {
    }

    public static void main(String[] args) {
        verifyMinimalTransportAuthContract();

        ChannelLaneState lanes = new ChannelLaneState();
        AtomicInteger heartbeats = new AtomicInteger();
        AtomicInteger schedulerFailures = new AtomicInteger();
        AtomicInteger taskExecutions = new AtomicInteger();

        lanes.authenticated(26L, false, "recovery_pending");
        lanes.updateTaskChannel(26L, false, "unavailable");
        for (int index = 0; index < 3; index++) {
            SafeScheduledTask.run(heartbeats::incrementAndGet, schedulerFailures::incrementAndGet);
        }
        assertEquals(3, heartbeats.get(),
                "outbox load failure cannot stop authenticated heartbeats");
        assertFalse(lanes.isTaskChannelReady(),
                "outbox load failure makes only the task lane unavailable");
        if (lanes.isTaskChannelReady()) {
            taskExecutions.incrementAndGet();
        }
        assertEquals(0, taskExecutions.get(),
                "outbox load failure must keep task execution disabled");

        lanes.updateTaskChannel(26L, false, "recovery_pending");
        SafeScheduledTask.run(heartbeats::incrementAndGet, schedulerFailures::incrementAndGet);
        assertEquals(4, heartbeats.get(),
                "pending replay failure cannot block the heartbeat lane");

        lanes.updateDeviceState(26L, ChannelLaneState.DEVICE_STALE);
        SafeScheduledTask.run(heartbeats::incrementAndGet, schedulerFailures::incrementAndGet);
        assertEquals(5, heartbeats.get(),
                "device snapshot failure cannot block the heartbeat lane");
        assertTrue(lanes.snapshot().transportAuthenticated,
                "device snapshot failure cannot retire transport");
        lanes.updateDeviceState(26L, ChannelLaneState.DEVICE_FRESH);
        assertEquals(ChannelLaneState.DEVICE_FRESH, lanes.snapshot().deviceState,
                "a recovered device snapshot can refresh its independent lane");

        AtomicInteger iterations = new AtomicInteger();
        CountDownLatch twoIterations = new CountDownLatch(2);
        ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
        scheduler.scheduleAtFixedRate(() -> SafeScheduledTask.run(() -> {
            int iteration = iterations.incrementAndGet();
            twoIterations.countDown();
            if (iteration == 1) {
                throw new IllegalStateException("fixture_once");
            }
        }, schedulerFailures::incrementAndGet), 0L, 20L, TimeUnit.MILLISECONDS);
        try {
            assertTrue(twoIterations.await(2L, TimeUnit.SECONDS),
                    "periodic wrapper did not reach the second iteration");
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new AssertionError("periodic wrapper test interrupted", interrupted);
        } finally {
            scheduler.shutdownNow();
        }
        assertTrue(iterations.get() >= 2,
                "one runnable exception cannot silently cancel the next period");
        assertEquals(1, schedulerFailures.get(), "one scheduler failure is classified");

        lanes.authenticated(27L, true, "ready");
        assertFalse(lanes.updateTaskChannel(26L, false, "unavailable"),
                "old epoch task callback cannot overwrite replacement state");
        assertFalse(lanes.updateDeviceState(26L, ChannelLaneState.DEVICE_STALE),
                "old epoch device callback cannot overwrite replacement state");
        assertTrue(lanes.isTaskChannelReady(), "replacement task lane remains ready");

        System.out.println("Channel separation self-test: PASS");
    }

    private static void verifyMinimalTransportAuthContract() {
        try {
            TransportAuthProof.Prepared proof = TransportAuthProof.prepare(
                    "device-26",
                    "challenge-26",
                    "nonce-26",
                    1_786_500_000L,
                    26,
                    2
            );
            assertEquals(
                    "{\"app_version_code\":26,\"contract\":\"code26_transport_v1\"," +
                            "\"task_store_protocol\":2}",
                    proof.canonicalTransportAuth,
                    "transport auth has only stable negotiation fields"
            );
            assertTrue(
                    proof.message.startsWith("oin-v1-wss-transport-auth\ndevice-26\n"),
                    "transport auth uses its own signature domain"
            );
            String request = proof.requestBody("test-signature");
            assertTrue(request.contains("\"transport_auth\":" + proof.canonicalTransportAuth),
                    "signed transport fields are sent unchanged");
            assertFalse(request.contains("\"payload\":"),
                    "minimal transport auth cannot contain a device snapshot payload");
        } catch (Exception failure) {
            throw new AssertionError("minimal transport auth fixture failed", failure);
        }
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }

    private static void assertFalse(boolean value, String message) {
        assertTrue(!value, message);
    }

    private static void assertEquals(int expected, int actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }

    private static void assertEquals(String expected, String actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }
}
