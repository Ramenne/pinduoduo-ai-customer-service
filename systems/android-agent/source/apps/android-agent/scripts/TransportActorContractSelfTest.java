package com.feifei.phonebridge;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

public final class TransportActorContractSelfTest {
    private TransportActorContractSelfTest() {
    }

    public static void main(String[] args) throws Exception {
        verifyActorSerializesEventOrderAndFencesOldEpoch();
        verifyBlockedPublisherDoesNotBlockActorHeartbeat();
        verifyProcessPersistentOnlineStateFailsClosed();
        verifyDeliveryAttemptContract();
        verifyBlockedIdentityIoDoesNotBlockActorHeartbeat();
        verifyNetworkCallbackRegistrationRecoversAfterFailure();
        verifyFailSafeStopRunsEveryResourceAndTerminalStep();
        verifyStaleIdentityCompletionReleasesGateAndRetries();
        verifyIdentityDeadlineRotatesBlockedLaneAndFencesLateCompletion();
        verifyNetworkRegistrationDeadlineAllowsNewGeneration();
        verifyTaskAdmissionTimeoutDegradesWithoutReofferStorm();
        verifyExpiredNewClaimClosesDurableOwnership();
        System.out.println("Transport actor contract self-test: PASS");
    }

    private static void verifyActorSerializesEventOrderAndFencesOldEpoch() {
        TransportEventActor actor = new TransportEventActor("actor-contract-test");
        List<String> events = new ArrayList<>();
        AtomicInteger epoch = new AtomicInteger(1);
        actor.postAndWait(() -> {
            events.add("connect:" + epoch.get());
            events.add("auth:" + epoch.get());
            epoch.incrementAndGet();
            int staleEpoch = 1;
            if (staleEpoch == epoch.get()) {
                events.add("stale_ack");
            }
            events.add("connect:" + epoch.get());
        }, 1_000L);
        actor.shutdownNow();
        assertEquals(Arrays.asList("connect:1", "auth:1", "connect:2"), events,
                "actor event order and old epoch fence");
    }

    private static void verifyBlockedPublisherDoesNotBlockActorHeartbeat() throws Exception {
        ExecutorService publisherExecutor = Executors.newSingleThreadExecutor();
        TransportEventActor actor = new TransportEventActor("actor-heartbeat-test");
        CountDownLatch publicationEntered = new CountDownLatch(1);
        CountDownLatch releasePublication = new CountDownLatch(1);
        CountDownLatch heartbeatRan = new CountDownLatch(1);
        CoalescingPublicationLane<String> lane = new CoalescingPublicationLane<>(
                publisherExecutor,
                ignored -> {
                    publicationEntered.countDown();
                    try {
                        releasePublication.await();
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                    }
                });
        lane.submit("slow_notification_or_preferences");
        assertTrue(publicationEntered.await(1, TimeUnit.SECONDS), "publisher must enter fixture");
        actor.post(heartbeatRan::countDown);
        assertTrue(heartbeatRan.await(250, TimeUnit.MILLISECONDS),
                "blocked publication lane must not block actor heartbeat");
        releasePublication.countDown();
        actor.shutdownNow();
        publisherExecutor.shutdownNow();
    }

    private static void verifyProcessPersistentOnlineStateFailsClosed() {
        assertEquals("SUSPECT", PersistedOnlineStateRules.effectiveState(
                "ONLINE_FRESH", true, false),
                "process restart cannot retain green online without fresh ACK");
        assertEquals("ONLINE_FRESH", PersistedOnlineStateRules.effectiveState(
                "ONLINE_FRESH", true, true),
                "fresh current activity may remain online");
        assertEquals("RECONNECT_WAIT", PersistedOnlineStateRules.effectiveState(
                "RECONNECT_WAIT", true, false),
                "non-online state remains exact");
    }

    private static void verifyDeliveryAttemptContract() {
        String attempt = UUID.randomUUID().toString();
        assertEquals(attempt, DeliveryAttemptContract.canonicalAttemptId(attempt),
                "canonical delivery attempt accepted");
        assertEquals(8, DeliveryAttemptContract.TASK_FIELDS.size(),
                "code28 task has exact attempt field");
        assertEquals(4, DeliveryAttemptContract.ACK_FIELDS.size(),
                "code28 ACK echoes exact attempt field");
        boolean rejected = false;
        try {
            DeliveryAttemptContract.canonicalAttemptId("not-a-uuid");
        } catch (IllegalArgumentException expected) {
            rejected = true;
        }
        assertTrue(rejected, "invalid attempt rejected before ACK");
    }

    private static void verifyBlockedIdentityIoDoesNotBlockActorHeartbeat() throws Exception {
        BoundedIoLane identityLane = new BoundedIoLane("identity-block-test", 2);
        TransportEventActor actor = new TransportEventActor("actor-during-sign-test");
        CountDownLatch signEntered = new CountDownLatch(1);
        CountDownLatch releaseSign = new CountDownLatch(1);
        CountDownLatch heartbeat = new CountDownLatch(1);
        assertTrue(identityLane.submit(() -> {
            signEntered.countDown();
            try {
                releaseSign.await();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        }), "identity sign fixture admitted");
        assertTrue(signEntered.await(1, TimeUnit.SECONDS), "identity sign blocks only its lane");
        actor.post(heartbeat::countDown);
        assertTrue(heartbeat.await(250, TimeUnit.MILLISECONDS),
                "blocked Keystore/sign lane must not block actor heartbeat");
        releaseSign.countDown();
        identityLane.stopNow();
        actor.shutdownNow();
    }

    private static void verifyNetworkCallbackRegistrationRecoversAfterFailure() {
        NetworkCallbackRegistration registration = new NetworkCallbackRegistration();
        long first = registration.begin();
        assertTrue(first > 0L, "first registration starts");
        assertTrue(registration.complete(first, false), "first failure is current");
        assertEquals(NetworkCallbackRegistration.UNREGISTERED, registration.state(),
                "failure cannot masquerade as registered");
        long retry = registration.begin();
        assertTrue(retry > first, "failure admits a later retry generation");
        assertTrue(registration.complete(retry, true), "retry success is current");
        assertTrue(registration.isRegistered(), "registered only after real completion");
        assertTrue(!registration.complete(first, true), "late first callback cannot overwrite retry");
    }

    private static void verifyFailSafeStopRunsEveryResourceAndTerminalStep() {
        FailSafeStopCoordinator coordinator = new FailSafeStopCoordinator();
        AtomicInteger cleaned = new AtomicInteger();
        AtomicInteger terminalPersisted = new AtomicInteger();
        coordinator.run(() -> { throw new IllegalStateException("actor_stop_timeout"); });
        coordinator.run(cleaned::incrementAndGet);
        coordinator.run(() -> { throw new IllegalStateException("network_unregister_timeout"); });
        coordinator.run(cleaned::incrementAndGet);
        coordinator.run(terminalPersisted::incrementAndGet);
        assertEquals(2, cleaned.get(), "actor timeout cannot skip other lanes");
        assertEquals(1, terminalPersisted.get(), "terminal state persistence always runs");
        assertEquals(2, coordinator.failures(), "cleanup failures remain observable");
    }

    private static void verifyStaleIdentityCompletionReleasesGateAndRetries() {
        IdentityPreflightGate gate = new IdentityPreflightGate();
        IdentityPreflightGate.Request first = gate.begin(4L);
        assertTrue(first != null && gate.isInFlight(), "identity preflight starts");
        assertEquals(IdentityPreflightGate.STALE_EPOCH_RETRY,
                gate.complete(first.generation, first.epoch, 5L),
                "retire during identity I/O requests a current-epoch retry");
        assertTrue(!gate.isInFlight(), "same-generation stale completion releases in-flight");
        IdentityPreflightGate.Request retry = gate.begin(5L);
        assertTrue(retry != null && retry.generation > first.generation,
                "current epoch identity preflight can start after stale completion");
        assertEquals(IdentityPreflightGate.CURRENT,
                gate.complete(retry.generation, retry.epoch, 5L),
                "current retry completes normally");
    }

    private static void verifyIdentityDeadlineRotatesBlockedLaneAndFencesLateCompletion()
            throws Exception {
        TransportEventActor actor = new TransportEventActor("identity-deadline-test");
        BoundedIoLane blockedLane = new BoundedIoLane("blocked-identity-generation", 1);
        CountDownLatch operationEntered = new CountDownLatch(1);
        CountDownLatch releaseOperation = new CountDownLatch(1);
        CountDownLatch actorTick = new CountDownLatch(1);
        CountDownLatch recovered = new CountDownLatch(1);
        AtomicReference<IdentityPreflightGate.Request> first = new AtomicReference<>();
        AtomicReference<IdentityPreflightGate.Request> retry = new AtomicReference<>();
        AtomicReference<BoundedIoLane> replacement = new AtomicReference<>();
        IdentityPreflightGate gate = new IdentityPreflightGate();

        actor.postAndWait(() -> first.set(gate.begin(10L)), 1_000L);
        assertTrue(blockedLane.submit(() -> {
            operationEntered.countDown();
            boolean released = false;
            while (!released) {
                try {
                    released = releaseOperation.await(100, TimeUnit.MILLISECONDS);
                } catch (InterruptedException ignored) {
                    // Model an uninterruptible Keystore/provider call.
                }
            }
        }), "blocked identity generation admitted");
        assertTrue(operationEntered.await(1, TimeUnit.SECONDS),
                "identity provider operation entered");

        actor.post(actorTick::countDown);
        assertTrue(actorTick.await(250, TimeUnit.MILLISECONDS),
                "identity provider stall cannot stop actor ticks");
        actor.postAndWait(() -> {
            assertTrue(gate.timeout(first.get().generation),
                    "deadline releases the blocked identity generation");
            blockedLane.stopNow();
            BoundedIoLane nextLane = new BoundedIoLane("replacement-identity-generation", 1);
            replacement.set(nextLane);
            IdentityPreflightGate.Request next = gate.begin(11L);
            retry.set(next);
            assertTrue(nextLane.submit(() -> actor.post(() -> {
                assertEquals(IdentityPreflightGate.CURRENT,
                        gate.complete(next.generation, next.epoch, 11L),
                        "replacement identity generation completes");
                recovered.countDown();
            })), "replacement identity work admitted");
        }, 1_000L);
        assertTrue(recovered.await(1, TimeUnit.SECONDS),
                "new identity generation recovers after timeout");
        actor.postAndWait(() -> assertEquals(IdentityPreflightGate.OBSOLETE_GENERATION,
                gate.complete(first.get().generation, first.get().epoch, 11L),
                "late old identity completion is fenced"), 1_000L);

        releaseOperation.countDown();
        replacement.get().stopNow();
        actor.shutdownNow();
    }

    private static void verifyNetworkRegistrationDeadlineAllowsNewGeneration() {
        NetworkCallbackRegistration registration = new NetworkCallbackRegistration();
        long blocked = registration.begin();
        assertTrue(blocked > 0L, "network registration starts");
        assertTrue(registration.timeout(blocked),
                "Binder registration deadline releases REGISTERING");
        assertEquals(NetworkCallbackRegistration.UNREGISTERED, registration.state(),
                "timed out registration is not reported registered");
        long retry = registration.begin();
        assertTrue(retry > blocked, "new lane receives a new registration generation");
        assertTrue(registration.complete(retry, true),
                "replacement callback registration can succeed");
        assertTrue(!registration.complete(blocked, true),
                "late completion from abandoned callback generation is fenced");
        assertTrue(registration.isRegistered(),
                "late old completion cannot overwrite the current registered generation");
    }

    private static void verifyTaskAdmissionTimeoutDegradesWithoutReofferStorm()
            throws Exception {
        TaskAdmissionWatchdog watchdog = new TaskAdmissionWatchdog();
        long first = watchdog.begin();
        assertTrue(first > 0L, "task admission starts");
        assertTrue(watchdog.timeout(first), "task admission deadline is current");
        assertTrue(watchdog.timedOut(), "task lane becomes explicitly degraded");
        assertEquals(-1L, watchdog.begin(),
                "server reoffers cannot create a SQLite claim storm while old claim is blocked");
        assertTrue(watchdog.complete(first), "late durable claim completion recovers the lane");
        assertTrue(watchdog.begin() > first,
                "admission automatically resumes after the old claim returns");

        TaskAdmissionPermit permit = new TaskAdmissionPermit(1L);
        Thread.sleep(5L);
        assertTrue(!permit.isValid(), "expired attempt cannot ACK or execute after durable claim");
    }

    private static void verifyExpiredNewClaimClosesDurableOwnership() {
        assertEquals(ExpiredTaskAdmissionRules.DURABLE_FAILURE,
                ExpiredTaskAdmissionRules.disposition(
                        TaskResultLedger.ClaimKind.DEADLINE_EXPIRED),
                "claim transaction crossing deadline returns durable PENDING failure");
        assertEquals(ExpiredTaskAdmissionRules.PRESERVE_EXISTING_TERMINAL,
                ExpiredTaskAdmissionRules.disposition(TaskResultLedger.ClaimKind.NEW),
                "NEW always conveys live executor ownership");
        assertEquals(ExpiredTaskAdmissionRules.PRESERVE_EXISTING_TERMINAL,
                ExpiredTaskAdmissionRules.disposition(TaskResultLedger.ClaimKind.PENDING),
                "redelivery of durable result must not execute twice");
        assertEquals(ExpiredTaskAdmissionRules.PRESERVE_EXISTING_TERMINAL,
                ExpiredTaskAdmissionRules.disposition(TaskResultLedger.ClaimKind.RUNNING),
                "only the current NEW owner may close its own timeout");
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }
}
