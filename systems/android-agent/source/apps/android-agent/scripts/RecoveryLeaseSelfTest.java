package com.feifei.phonebridge;

import java.io.File;
import java.nio.file.Files;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

public final class RecoveryLeaseSelfTest {
    public static void main(String[] args) throws Exception {
        File directory = Files.createTempDirectory("oin-recovery-lease-").toFile();
        RecoveryLeaseStore store = new RecoveryLeaseStore(directory);
        RecoveryLeaseStore secondStore = new RecoveryLeaseStore(directory);

        RecoveryLease first = store.beginProcess("process-a", 100L, 200L);
        require(first.generation == 1L, "first generation");
        require(first.isOwnedByMain(), "main owns initial lease");
        require(store.claimExpired(1L, "process-a", 199L, 249L,
                "guardian:pending:liveness:early") == null,
                "lease cannot be claimed early");

        RecoveryLease progressed = store.recordProgress(1L, "process-a", 180L, 280L);
        require(progressed != null && progressed.deadlineElapsed == 280L,
                "progress extends same generation");
        require(store.claimExpired(1L, "process-a", 200L, 250L,
                "guardian:pending:liveness:stale") == null,
                "stale alarm cannot claim a renewed lease");

        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        AtomicInteger claims = new AtomicInteger();
        AtomicReference<Throwable> threadFailure = new AtomicReference<>();
        Runnable claimant = () -> {
            ready.countDown();
            try {
                start.await();
                RecoveryLeaseStore claimantStore = Thread.currentThread().getName().equals("left")
                        ? store : secondStore;
                if (claimantStore.claimExpired(1L, "process-a", 281L, 331L,
                        "guardian:pending:liveness:" + UUID.randomUUID()) != null) {
                    claims.incrementAndGet();
                }
            } catch (Exception failure) {
                threadFailure.compareAndSet(null, failure);
            }
        };
        Thread left = new Thread(claimant, "left");
        Thread right = new Thread(claimant, "right");
        left.start();
        right.start();
        ready.await();
        start.countDown();
        left.join();
        right.join();
        require(threadFailure.get() == null, "concurrent claim must not throw");
        require(claims.get() == 1, "guardian claim must be single-owner");

        RecoveryLease claimed = store.read();
        require(!claimed.isOwnedByMain(), "claim transfers recovery ownership");
        require(claimed.deadlineElapsed == 331L,
                "claim installs a distinct handoff deadline");
        require(store.recordProgress(1L, "process-a", 282L, 382L) == null,
                "main cannot overwrite claimed recovery");
        require(store.recordAuthenticatedProgress(
                1L, "process-a", 282L, 382L) == null,
                "late progress cannot steal an exact guardian claim");
        RecoveryLease resumed = store.resumeExactClaim(
                1L, "process-a", claimed.recoveryOwner, 283L, 383L);
        require(resumed != null && resumed.isOwnedByMain(),
                "same live process resumes exact guardian claim");
        require(store.resumeExactClaim(
                1L, "process-a", "guardian:wrong", 284L, 384L) == null,
                "wrong guardian owner cannot resume claim");
        RecoveryLease blocked = store.blockGeneration(
                1L, "process-a", RecoveryLease.OWNER_NETWORK_BLOCKED);
        require(blocked != null && !blocked.isOwnedByMain(), "offline blocks guardian budget");
        require(store.claimExpired(1L, "process-a", 999L, 1049L,
                "guardian:pending:liveness:offline") == null,
                "offline lease cannot trigger recovery storm");
        require(store.resumeBlocked(
                1L, "process-a", RecoveryLease.OWNER_NETWORK_BLOCKED, 300L, 400L)
                .isOwnedByMain(), "network available starts a fresh bounded episode");
        RecoveryLease terminal = store.blockGeneration(
                1L, "process-a", RecoveryLease.OWNER_TERMINAL_BLOCKED);
        require(terminal != null && RecoveryLease.OWNER_TERMINAL_BLOCKED.equals(
                terminal.recoveryOwner),
                "terminal state atomically blocks the generation");
        require(store.resumeBlocked(
                1L, "process-a", RecoveryLease.OWNER_NETWORK_BLOCKED, 301L, 401L) == null,
                "network availability cannot revive a terminal generation");
        RecoveryLease second = store.beginProcess("process-b", 400L, 500L);
        require(second.generation == 2L, "new process increments generation");
        require(store.claimExpired(1L, "process-a", 999L, 1049L,
                "guardian:pending:liveness:stale") == null,
                "old process alarm cannot claim new generation");
        require(store.stop(2L, "process-b"), "stop clears current lease owner");
        require(store.claimExpired(2L, "process-b", 999L, 1049L,
                "guardian:pending:liveness:after-stop") == null,
                "stopped lease cannot restart service");
        RecoveryLease third = store.beginProcess("process-c", 1_000L, 1_055L);
        require(third.generation == 3L && third.isOwnedByMain(),
                "explicit re-enable establishes a fresh main generation");
        require(!store.stop(2L, "process-b"),
                "late stop from the disabled generation cannot stop re-enabled runtime");
        require(store.claimExpired(3L, "process-c", 1_055L, 1_070L,
                "guardian:pending:liveness:after-re-enable") != null,
                "re-enabled generation retains guardian recovery");

        verifyClaimCannotOverrideBlock(RecoveryLease.OWNER_NETWORK_BLOCKED);
        verifyClaimCannotOverrideBlock(RecoveryLease.OWNER_TERMINAL_BLOCKED);
        verifyRejectedStartIsObservableAndCannotResume();
        verifyHandoffTimeoutIsAuthoritative();
        verifyInfrastructureRecoveryIsSingleOwner();
        verifyPreClaimFailureCanBeMadeObservable();
        verifyPreClaimRetryBudget();
        verifyAlarmIdentityIsGenerationScoped();

        System.out.println("Recovery lease self-test: PASS");
    }

    private static void verifyClaimCannotOverrideBlock(String blockedOwner) throws Exception {
        File directory = Files.createTempDirectory("oin-recovery-lease-interleaving-").toFile();
        RecoveryLeaseStore store = new RecoveryLeaseStore(directory);
        RecoveryLease lease = store.beginProcess("process-interleaving", 10L, 20L);
        RecoveryLease claimed = store.claimExpired(
                lease.generation,
                lease.processInstanceId,
                20L,
                30L,
                "guardian:pending:liveness:interleaving");
        require(claimed != null, "guardian owns expired interleaving lease");

        RecoveryLease blocked = store.blockGeneration(
                lease.generation,
                lease.processInstanceId,
                blockedOwner);
        require(blocked != null && blockedOwner.equals(blocked.recoveryOwner),
                "block overrides an in-flight guardian claim");
        require(store.resumeExactClaim(
                lease.generation,
                lease.processInstanceId,
                claimed.recoveryOwner,
                21L,
                31L) == null,
                "stale guardian claim cannot revive a blocked generation");
    }

    private static void verifyRejectedStartIsObservableAndCannotResume() throws Exception {
        File directory = Files.createTempDirectory("oin-recovery-start-rejected-").toFile();
        RecoveryLeaseStore store = new RecoveryLeaseStore(directory);
        RecoveryLease lease = store.beginProcess("process-rejected", 10L, 20L);
        String owner = "guardian:pending:liveness:rejected";
        RecoveryLease claim = store.claimExpired(
                lease.generation, lease.processInstanceId, 20L, 35L, owner);
        require(claim != null, "expired lease enters handoff pending");
        RecoveryLease blocked = store.failExactGuardianHandoff(
                lease.generation,
                lease.processInstanceId,
                owner,
                RecoveryLease.OWNER_GUARDIAN_START_REJECTED);
        require(blocked != null && RecoveryLease.OWNER_GUARDIAN_START_REJECTED.equals(
                blocked.recoveryOwner), "rejected FGS start has a persistent terminal outcome");
        require(store.resumeExactClaim(
                lease.generation, lease.processInstanceId, owner, 21L, 76L) == null,
                "rejected handoff cannot be acknowledged later");
        RecoveryLease recovered = store.recordAuthenticatedProgress(
                lease.generation, lease.processInstanceId, 22L, 77L);
        require(recovered != null && recovered.isOwnedByMain(),
                "real authenticated progress may recover a guardian-only failure");
    }

    private static void verifyHandoffTimeoutIsAuthoritative() throws Exception {
        File directory = Files.createTempDirectory("oin-recovery-handoff-timeout-").toFile();
        RecoveryLeaseStore store = new RecoveryLeaseStore(directory);
        RecoveryLease lease = store.beginProcess("process-timeout", 100L, 110L);
        String owner = "guardian:pending:liveness:timeout";
        require(store.claimExpired(
                lease.generation, lease.processInstanceId, 110L, 125L, owner) != null,
                "handoff timeout fixture claims lease");
        RecoveryLease expired = store.resumeExactClaim(
                lease.generation, lease.processInstanceId, owner, 126L, 181L);
        require(expired != null && RecoveryLease.OWNER_GUARDIAN_HANDOFF_TIMEOUT.equals(
                expired.recoveryOwner), "late service handoff persists timeout instead of reviving");
        require(store.expireExactGuardianHandoff(
                lease.generation, lease.processInstanceId, owner, 126L) == null,
                "handoff timeout transition is exact and idempotent");
    }

    private static void verifyInfrastructureRecoveryIsSingleOwner() throws Exception {
        File directory = Files.createTempDirectory("oin-recovery-infrastructure-").toFile();
        RecoveryLeaseStore store = new RecoveryLeaseStore(directory);
        RecoveryLease lease = store.beginProcess("process-infrastructure", 200L, 255L);
        RecoveryLease requested = store.requestImmediateRecovery(
                lease.generation,
                lease.processInstanceId,
                205L,
                "main:recovery:infrastructure:identity_io_rotation_exhausted");
        require(requested != null && requested.isGuardianClaimable(),
                "lane exhaustion remains guardian-recoverable rather than terminal");
        require(store.recordAuthenticatedProgress(
                lease.generation, lease.processInstanceId, 206L, 261L) == null,
                "ordinary progress cannot erase a pending infrastructure recovery");
        RecoveryLease claimed = store.claimExpired(
                lease.generation,
                lease.processInstanceId,
                205L,
                220L,
                "guardian:pending:infrastructure:identity_io_rotation_exhausted");
        require(claimed != null, "guardian can claim immediate infrastructure recovery");

        File failedDirectory = Files.createTempDirectory(
                "oin-recovery-infrastructure-failed-").toFile();
        RecoveryLeaseStore failedStore = new RecoveryLeaseStore(failedDirectory);
        RecoveryLease failedLease = failedStore.beginProcess("process-failed", 300L, 355L);
        String requestOwner = "main:recovery:infrastructure:network_io_rotation_exhausted";
        require(failedStore.requestImmediateRecovery(
                failedLease.generation, failedLease.processInstanceId, 305L, requestOwner) != null,
                "failed infrastructure fixture enters requested state");
        RecoveryLease failed = failedStore.failImmediateRecovery(
                failedLease.generation, failedLease.processInstanceId, requestOwner);
        require(failed != null && RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE.equals(
                failed.recoveryOwner), "exhausted infrastructure persistence is observable");
    }

    private static void verifyPreClaimFailureCanBeMadeObservable() throws Exception {
        File directory = Files.createTempDirectory("oin-recovery-preclaim-failure-").toFile();
        RecoveryLeaseStore store = new RecoveryLeaseStore(directory);
        RecoveryLease lease = store.beginProcess("process-preclaim", 400L, 410L);
        RecoveryLease blocked = store.blockExpiredMainAfterGuardianFailure(
                lease.generation,
                lease.processInstanceId,
                411L,
                RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE);
        require(blocked != null && RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE.equals(
                blocked.recoveryOwner),
                "pre-claim storage failure does not leave expired MAIN silently unarmed");
        require(store.blockExpiredMainAfterGuardianFailure(
                lease.generation,
                lease.processInstanceId,
                412L,
                RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE) == null,
                "pre-claim failure transition is one-way and exact");
    }

    private static void verifyPreClaimRetryBudget() {
        require(RecoveryGuardianRetryPolicy.mayRetry(0), "first pre-claim failure retries");
        require(RecoveryGuardianRetryPolicy.delayMillis(0) == 2_000L,
                "first pre-claim retry delay is bounded");
        require(RecoveryGuardianRetryPolicy.mayRetry(1), "second pre-claim failure retries");
        require(RecoveryGuardianRetryPolicy.delayMillis(1) == 4_000L,
                "second pre-claim retry backs off once");
        require(!RecoveryGuardianRetryPolicy.mayRetry(2),
                "pre-claim storage failure never creates an alarm loop");
        require(RecoveryGuardianRetryPolicy.delayMillis(2) == -1L,
                "exhausted pre-claim retry has no deadline");
    }

    private static void verifyAlarmIdentityIsGenerationScoped() {
        String generationOne = RecoveryAlarmIdentity.dataUri(1L, "process-a", "liveness");
        String generationTwo = RecoveryAlarmIdentity.dataUri(2L, "process-a", "liveness");
        String processTwo = RecoveryAlarmIdentity.dataUri(1L, "process-b", "liveness");
        require(generationOne.equals(RecoveryAlarmIdentity.dataUri(
                        1L, "process-a", "liveness")),
                "same generation/process reuses one PendingIntent identity for retry/handoff");
        require(!generationOne.equals(generationTwo),
                "old generation cannot overwrite or cancel new generation Alarm");
        require(!generationOne.equals(processTwo),
                "same numeric generation in another process cannot share Alarm identity");
        require(!generationOne.contains("process-a"),
                "alarm identity does not expose process instance id");
        String preclaim = RecoveryAlarmIdentity.dataUri(1L, "process-a", "preclaim");
        String handoff = RecoveryAlarmIdentity.dataUri(1L, "process-a", "handoff");
        require(!generationOne.equals(preclaim) && !generationOne.equals(handoff) &&
                        !preclaim.equals(handoff),
                "same generation phases cannot overwrite or cancel each other's Alarm");
        require(preclaim.equals(RecoveryAlarmIdentity.dataUri(
                        1L, "process-a", "preclaim")),
                "bounded preclaim retries reuse only the preclaim Alarm identity");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
