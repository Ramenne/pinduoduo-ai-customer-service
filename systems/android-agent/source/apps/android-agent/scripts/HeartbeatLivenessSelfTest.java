package com.feifei.phonebridge;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;

public final class HeartbeatLivenessSelfTest {
    private static final long SUSPECT_AFTER_MILLIS = CloudChannelTiming.SUSPECT_AFTER_MILLIS;
    private static final long RETIRE_AFTER_MILLIS = CloudChannelTiming.RETIRE_AFTER_MILLIS;
    private static final long SERVER_TIMEOUT_MILLIS =
            CloudChannelTiming.SERVER_CLIENT_INBOUND_SILENCE_TIMEOUT_MILLIS;

    private HeartbeatLivenessSelfTest() {
    }

    public static void main(String[] args) {
        Object oldSocket = new Object();
        Object newSocket = new Object();
        ServerActivityWatchdog<Object> watchdog = new ServerActivityWatchdog<>(
                SUSPECT_AFTER_MILLIS,
                RETIRE_AFTER_MILLIS
        );

        assertTrue(SUSPECT_AFTER_MILLIS < RETIRE_AFTER_MILLIS,
                "probe/grace must follow the suspect boundary");
        assertEquals(90_000L, SERVER_TIMEOUT_MILLIS,
                "server inbound-silence timeout remains an independent direction");

        assertFalse(watchdog.isStale(oldSocket, 100_000L), "uninitialized tracker must be inert");

        watchdog.begin(oldSocket, 1_000L);
        assertFalse(watchdog.isStale(oldSocket, 1_000L + SUSPECT_AFTER_MILLIS - 1L),
                "activity is fresh before the suspect boundary");
        assertEquals(ServerActivityWatchdog.Health.SUSPECT,
                watchdog.health(oldSocket, 1_000L + SUSPECT_AFTER_MILLIS),
                "suspect boundary enters SUSPECT without cancelling the socket");
        assertFalse(watchdog.isRetireDue(oldSocket, 1_000L + SUSPECT_AFTER_MILLIS),
                "suspect boundary cannot retire the socket");

        // The supervisor observes SUSPECT first and schedules a probe. A delayed ACK then wins
        // before the hard deadline and returns the same epoch to ONLINE_FRESH.
        assertTrue(watchdog.acknowledge(oldSocket, 20_000L), "current socket ACK must refresh activity");
        assertFalse(watchdog.isStale(oldSocket, 20_000L + SUSPECT_AFTER_MILLIS - 1L),
                "ACK refreshes the deadline");
        assertEquals(ServerActivityWatchdog.Health.FRESH,
                watchdog.health(oldSocket, 20_000L + SUSPECT_AFTER_MILLIS - 1L),
                "delayed ACK recovers after the supervisor's suspect observation");

        watchdog.begin(newSocket, 200_000L);
        assertFalse(
                watchdog.acknowledge(oldSocket, 220_000L),
                "late ACK from replaced socket must be rejected"
        );
        assertEquals(ServerActivityWatchdog.Health.SUSPECT,
                watchdog.health(newSocket, 200_000L + SUSPECT_AFTER_MILLIS),
                "old socket cannot keep the new socket healthy");
        assertTrue(watchdog.isRetireDue(newSocket, 200_000L + RETIRE_AFTER_MILLIS),
                "current socket retires only after the full grace window");

        watchdog.clear(oldSocket);
        assertTrue(
                watchdog.isStale(newSocket, 200_000L + SUSPECT_AFTER_MILLIS),
                "clearing an old socket must not clear the active session"
        );
        watchdog.clear(newSocket);
        assertFalse(watchdog.isStale(newSocket, 999_000L), "closed session must be inert");

        assertTrue(
                ServerActivityWatchdog.isFreshWallClock(
                        10_000L, 10_000L + SUSPECT_AFTER_MILLIS - 1L,
                        SUSPECT_AFTER_MILLIS),
                "UI may show green only inside the freshness window"
        );
        assertFalse(
                ServerActivityWatchdog.isFreshWallClock(
                        10_000L, 10_000L + SUSPECT_AFTER_MILLIS,
                        SUSPECT_AFTER_MILLIS),
                "UI must reject a stale activity timestamp"
        );
        assertFalse(
                ServerActivityWatchdog.isFreshWallClock(
                        70_000L, 65_000L, SUSPECT_AFTER_MILLIS),
                "wall-clock rollback must fail closed for green status"
        );
        assertEquals(
                -1L,
                ServerActivityWatchdog.displayAgeMillis(70_000L, 65_000L),
                "invalid wall-clock age must be unknown"
        );

        verifyReconnectBackoffSurvivesTransientNoNetwork();
        verifyAckInterleavingCannotBeOverwrittenByStaleSuspect();
        verifyConcurrentSupervisorTicksCannotStartTwoSockets();
        verifySupervisorRuntimeFailureDoesNotEscape();
        verifyStaleMessageCannotPublishState();
        verifyEverySupervisorReconnectReasonCanRetire();
        verifyReconnectIsRegisteredBeforeRetirement();
        verifyServiceWatchdogRescuesAStalledHeartbeatExecutor();
        verifyRecoveryBudgetCoversDetectionAndReconnect();
        verifyObservedDisconnectBudgetAndEpisodeHistory();
        verifyFailureStagesAreDeterministic();

        System.out.println("Heartbeat liveness self-test: PASS");
    }

    private static void verifyReconnectBackoffSurvivesTransientNoNetwork() {
        BoundedReconnectBackoff backoff = new BoundedReconnectBackoff(60_000L);
        assertEquals(2_000L, backoff.schedule(1_000L, 0L), "first retry is bounded");
        assertFalse(backoff.claimIfDue(1_999L), "retry cannot run before monotonic deadline");
        assertTrue(backoff.claimIfDue(2_000L), "deadline admits one connection attempt");
        assertFalse(backoff.claimIfDue(2_000L), "claimed deadline cannot admit a second attempt");

        // The admitted attempt observes a transient no-network result. It must create another
        // deadline rather than leaving socket=null with no future transition.
        assertEquals(4_000L, backoff.schedule(2_000L, 0L),
                "transient no-network schedules the next bounded retry");
        assertTrue(backoff.claimIfDue(4_000L), "later supervisor tick retries without callback");

        backoff.schedule(4_000L, 0L);
        backoff.retryNow(4_100L);
        assertTrue(backoff.claimIfDue(4_100L), "network callback may only advance the deadline");
        backoff.authenticated();
        assertEquals(6_000L, backoff.schedule(5_000L, 0L),
                "authentication resets exponential backoff");
    }

    private static void verifyAckInterleavingCannotBeOverwrittenByStaleSuspect() {
        Object socket = new Object();
        ServerActivityWatchdog<Object> watchdog = new ServerActivityWatchdog<>(
                SUSPECT_AFTER_MILLIS,
                RETIRE_AFTER_MILLIS
        );
        CloudConnectionLifecycle lifecycle = new CloudConnectionLifecycle(
                CloudChannelTiming.MAX_RECONNECT_BACKOFF_MILLIS);
        watchdog.begin(socket, 1_000L);
        lifecycle.markOnlineFresh();

        ServerActivityWatchdog.Observation<Object> staleObservation =
                watchdog.observe(socket, 1_000L + SUSPECT_AFTER_MILLIS);
        assertEquals(ServerActivityWatchdog.Health.SUSPECT, staleObservation.health,
                "supervisor observes the pre-ACK activity generation as suspect");

        assertTrue(watchdog.acknowledge(socket, 1_001L + SUSPECT_AFTER_MILLIS),
                "interleaved ACK advances the activity generation");
        lifecycle.markOnlineFresh();
        if (watchdog.isCurrent(staleObservation)) {
            lifecycle.markSuspect("heartbeat_ack_suspect");
        }
        assertEquals(CloudConnectionLifecycle.ONLINE_FRESH, lifecycle.snapshot().state,
                "stale supervisor observation cannot overwrite ACK-fresh state");

        lifecycle.prepareBackoffReconnect(60_000L, 0L, "fixture_disconnect");
        lifecycle.enterReconnectWait("fixture_disconnect");
        assertTrue(lifecycle.claimReconnectIfDue(lifecycle.retryAtElapsedMillis()),
                "current reconnect cycle records an admitted attempt");
        assertEquals(1L, lifecycle.snapshot().reconnectAttempt,
                "attempt count belongs to the active reconnect cycle");
        assertEquals(0L, lifecycle.markOnlineFresh().reconnectAttempt,
                "authentication resets the current reconnect-cycle attempt count");
    }

    private static void verifyConcurrentSupervisorTicksCannotStartTwoSockets() {
        ConnectionAttemptGate gate = new ConnectionAttemptGate();
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(12);
        AtomicInteger admitted = new AtomicInteger();
        List<Thread> threads = new ArrayList<>();
        for (int index = 0; index < 12; index++) {
            Thread thread = new Thread(() -> {
                try {
                    start.await();
                    if (gate.tryStart(7L)) {
                        admitted.incrementAndGet();
                    }
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                } finally {
                    done.countDown();
                }
            });
            threads.add(thread);
            thread.start();
        }
        start.countDown();
        try {
            done.await();
            for (Thread thread : threads) {
                thread.join();
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new AssertionError("concurrency fixture interrupted", interrupted);
        }
        assertEquals(1L, admitted.get(), "concurrent ticks admit exactly one socket attempt");
        assertTrue(gate.finish(7L), "active attempt can finish");
        assertTrue(gate.tryStart(8L), "replacement attempt starts after the old attempt finishes");
        assertFalse(gate.finish(7L), "stale callback cannot finish the replacement attempt");
        assertTrue(gate.isActive(), "replacement remains active after stale callback");
        assertTrue(gate.finish(8L), "current callback finishes the replacement attempt");
    }

    private static void verifySupervisorRuntimeFailureDoesNotEscape() {
        AtomicInteger iterations = new AtomicInteger();
        AtomicInteger failures = new AtomicInteger();
        SupervisorIterationGuard.run(() -> {
            iterations.incrementAndGet();
            throw new IllegalStateException("fixture_failure");
        }, () -> {
            failures.incrementAndGet();
            throw new IllegalStateException("fixture_diagnostic_failure");
        });
        SupervisorIterationGuard.run(iterations::incrementAndGet, failures::incrementAndGet);
        assertEquals(2L, iterations.get(), "next supervisor iteration still runs after RuntimeException");
        assertEquals(1L, failures.get(), "failure handler is isolated from the scheduler");
    }

    private static void verifyStaleMessageCannotPublishState() {
        Object oldSocket = new Object();
        Object newSocket = new Object();
        assertTrue(ConnectionCallbackFence.mayPublishAuthenticatedMessage(
                8L, 8L, newSocket, newSocket, true),
                "current authenticated epoch may publish state");
        assertFalse(ConnectionCallbackFence.mayPublishAuthenticatedMessage(
                7L, 8L, oldSocket, newSocket, true),
                "old epoch cannot publish over replacement state");
        assertFalse(ConnectionCallbackFence.mayPublishAuthenticatedMessage(
                8L, 8L, oldSocket, newSocket, true),
                "old socket cannot publish in current epoch");
        assertFalse(ConnectionCallbackFence.mayPublishAuthenticatedMessage(
                8L, 8L, newSocket, newSocket, false),
                "unauthenticated callback cannot publish state");
    }

    private static void verifyEverySupervisorReconnectReasonCanRetire() {
        assertTrue(SocketRetirementRules.mayRetire(
                "task_result_delivery_reconnect", true, false, -1L, 70_000L, 30_000L),
                "outbox send failure must retire the current socket");
        assertTrue(SocketRetirementRules.mayRetire(
                "heartbeat_failed", true, false, -1L, 70_000L, 30_000L),
                "heartbeat send failure must retire the current socket");
        assertFalse(SocketRetirementRules.mayRetire(
                "heartbeat_ack_grace_exhausted", true, false, -1L, 80_000L, 30_000L),
                "heartbeat hard deadline must be rechecked at retirement");
        assertTrue(SocketRetirementRules.mayRetire(
                "heartbeat_ack_grace_exhausted", true, true, -1L, 80_000L, 30_000L),
                "grace-exhausted heartbeat retires the socket");
        assertFalse(SocketRetirementRules.mayRetire(
                "cloud_auth_timeout", false, false, 50_000L,
                50_000L + CloudChannelTiming.AUTHENTICATION_TIMEOUT_MILLIS - 1L,
                CloudChannelTiming.AUTHENTICATION_TIMEOUT_MILLIS),
                "authentication cannot retire early");
        assertTrue(SocketRetirementRules.mayRetire(
                "cloud_auth_timeout", false, false, 50_000L,
                50_000L + CloudChannelTiming.AUTHENTICATION_TIMEOUT_MILLIS,
                CloudChannelTiming.AUTHENTICATION_TIMEOUT_MILLIS),
                "authentication deadline retires exactly at the boundary");
    }

    private static void verifyReconnectIsRegisteredBeforeRetirement() {
        CloudConnectionLifecycle lifecycle = new CloudConnectionLifecycle(
                CloudChannelTiming.MAX_RECONNECT_BACKOFF_MILLIS);
        assertEquals(CloudConnectionLifecycle.ONLINE_FRESH,
                lifecycle.markOnlineFresh().state,
                "authenticated socket enters ONLINE_FRESH");
        assertEquals(CloudConnectionLifecycle.SUSPECT,
                lifecycle.markSuspect("heartbeat_ack_suspect").state,
                "soft deadline enters SUSPECT");

        CloudConnectionLifecycle.Snapshot prepared = lifecycle.prepareBackoffReconnect(
                80_000L, 0L, "heartbeat_ack_grace_exhausted");
        assertTrue(prepared.retryAtElapsedMillis > 80_000L,
                "retirement prepares a bounded reconnect deadline first");
        CloudConnectionLifecycle.Snapshot waiting = lifecycle.enterReconnectWait(
                "heartbeat_ack_grace_exhausted");
        assertEquals(CloudConnectionLifecycle.RECONNECT_WAIT, waiting.state,
                "transport cancellation observes RECONNECT_WAIT");

        // Simulated immediate cancel callback cannot erase the already registered deadline.
        assertTrue(lifecycle.hasReconnectDeadline(),
                "cancel callback cannot leave socket-null with no retry");
        assertFalse(lifecycle.claimReconnectIfDue(waiting.retryAtElapsedMillis - 1L),
                "replacement cannot start before its deadline");
        assertTrue(lifecycle.claimReconnectIfDue(waiting.retryAtElapsedMillis),
                "retired socket has a bounded replacement attempt");
        assertEquals(CloudConnectionLifecycle.CONNECTING, lifecycle.snapshot().state,
                "due replacement enters CONNECTING");
    }

    private static void verifyServiceWatchdogRescuesAStalledHeartbeatExecutor() {
        ServiceTransportWatchdog watchdog = new ServiceTransportWatchdog();
        watchdog.recordHeartbeatAttempt(10_000L);
        assertFalse(
                watchdog.claimHeartbeatRescue(44_999L, true),
                "service watchdog must not duplicate a healthy heartbeat cadence"
        );
        assertTrue(
                watchdog.claimHeartbeatRescue(45_000L, true),
                "independent service tick rescues a heartbeat executor after 35 seconds"
        );
        assertFalse(
                watchdog.claimHeartbeatRescue(79_999L, true),
                "a rescue attempt resets the monotonic rescue deadline"
        );
        assertTrue(
                watchdog.claimHeartbeatRescue(80_000L, true),
                "a still-stalled executor is rescued again on a later service tick"
        );
        assertFalse(
                watchdog.claimHeartbeatRescue(200_000L, false),
                "service watchdog cannot send before the socket is authenticated"
        );
    }

    private static void verifyRecoveryBudgetCoversDetectionAndReconnect() {
        RecoveryEpisode episode = new RecoveryEpisode(
                CloudChannelTiming.ORDINARY_AVAILABILITY_BUDGET_MILLIS);
        RecoveryEpisode.Snapshot started = episode.startIfIdle(
                10_000L,
                "heartbeat_ack_suspect",
                CloudChannelTiming.ORDINARY_AVAILABILITY_BUDGET_MILLIS);
        assertEquals(65_000L, started.deadlineElapsedMillis,
                "half-open budget starts at last authenticated activity");
        assertTrue(CloudChannelTiming.RETIRE_AFTER_MILLIS <
                        CloudChannelTiming.ORDINARY_AVAILABILITY_BUDGET_MILLIS,
                "retirement leaves a bounded reconnect remainder");
        assertEquals(RecoveryEpisode.BUDGET_EXHAUSTED,
                episode.recovered(65_000L).outcome,
                "late task-ready cannot be mislabeled recovered");
    }

    private static void verifyObservedDisconnectBudgetAndEpisodeHistory() {
        RecoveryEpisode episode = new RecoveryEpisode(
                CloudChannelTiming.ORDINARY_AVAILABILITY_BUDGET_MILLIS);
        RecoveryEpisode.Snapshot started = episode.startIfIdle(
                20_000L,
                "transport_failure",
                CloudChannelTiming.OBSERVED_DISCONNECT_RECOVERY_BUDGET_MILLIS);
        assertEquals(60_000L, started.deadlineElapsedMillis,
                "explicit disconnect receives the 40-second recovery budget");
        episode.recordAttempt();
        episode.recordFailure("dns");
        RecoveryEpisode.Snapshot recovered = episode.recovered(40_000L);
        assertEquals(RecoveryEpisode.RECOVERED, recovered.outcome,
                "task-channel-ready inside budget closes the episode");
        assertEquals(1L, recovered.attempts, "completed episode retains attempts");
        assertEquals("dns", recovered.failureStage, "completed episode retains last failure");
    }

    private static void verifyFailureStagesAreDeterministic() {
        assertEquals("dns", TransportFailureClassifier.classify(
                new java.net.UnknownHostException("fixture"), 0), "DNS is distinct");
        assertEquals("tls", TransportFailureClassifier.classify(
                new javax.net.ssl.SSLException("fixture"), 0), "TLS is distinct");
        assertEquals("http_redirect", TransportFailureClassifier.classify(null, 308),
                "HTTP redirect cannot be followed as transport auth");
        assertEquals("websocket_upgrade", TransportFailureClassifier.classify(null, 403),
                "failed websocket upgrade is distinct");
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }

    private static void assertFalse(boolean value, String message) {
        assertTrue(!value, message);
    }

    private static void assertEquals(long expected, long actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }
}
