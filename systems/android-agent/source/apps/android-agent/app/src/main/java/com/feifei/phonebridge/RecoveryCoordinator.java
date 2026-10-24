package com.feifei.phonebridge;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.SystemClock;

import java.io.File;
import java.io.IOException;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The only process-level recovery coordinator. It never owns a socket: it maintains the
 * cross-process lease and translates recovery signals into events for the one cloud actor.
 */
final class RecoveryCoordinator {
    static final String ACTION_GUARDIAN = "com.feifei.phonebridge.GUARDIAN_RECOVERY";
    static final String EXTRA_GENERATION = "recovery_generation";
    static final String EXTRA_PROCESS_INSTANCE_ID = "recovery_process_instance_id";
    static final String EXTRA_GUARDIAN_OWNER = "recovery_guardian_owner";
    static final String EXTRA_GUARDIAN_PRECLAIM_RETRIES =
            "recovery_guardian_preclaim_retries";
    static final long LEASE_WINDOW_MILLIS = 55_000L;
    static final long HANDOFF_WINDOW_MILLIS = 15_000L;
    static final int GUARDIAN_REQUEST_CODE = 9029;
    private static final int MAX_CRITICAL_WRITE_RETRIES = 3;

    private final Context context;
    private final RecoveryLeaseStore store;
    private final BoundedIoLane leaseLane = new BoundedIoLane("oin-recovery-coordinator", 8);
    private final ScheduledExecutorService criticalLeaseWriter =
            Executors.newSingleThreadScheduledExecutor(runnable -> {
                Thread thread = new Thread(runnable, "oin-recovery-critical-writer");
                thread.setDaemon(true);
                return thread;
            });
    private final AtomicBoolean criticalDrainScheduled = new AtomicBoolean(false);
    private final AtomicBoolean terminalBlockRequested = new AtomicBoolean(false);
    private final AtomicBoolean terminalBlockApplied = new AtomicBoolean(false);
    private final AtomicBoolean authenticatedProgressRequested = new AtomicBoolean(false);
    private final AtomicReference<Boolean> networkAvailabilityRequested = new AtomicReference<>();
    private final AtomicReference<Boolean> latestNetworkAvailability = new AtomicReference<>();
    private final AtomicReference<String> infrastructureRecoveryReason = new AtomicReference<>();
    private final AtomicBoolean infrastructureRecoveryScheduled = new AtomicBoolean(false);
    private final AtomicBoolean infrastructureRecoveryConsumed = new AtomicBoolean(false);
    private final AtomicInteger criticalWriteRetryCount = new AtomicInteger(0);
    private volatile CloudChannelClient channel;
    private volatile long generation = -1L;
    private volatile String processInstanceId = "";
    private volatile boolean stopped;
    private volatile boolean preserveRecoveryAfterAttachFailure;

    RecoveryCoordinator(Context context) {
        this.context = context.getApplicationContext();
        File directory = new File(this.context.getNoBackupFilesDir(), "oin-recovery");
        this.store = new RecoveryLeaseStore(directory);
    }

    boolean attach(CloudChannelClient cloudChannel) {
        stopped = false;
        preserveRecoveryAfterAttachFailure = false;
        String instance = CloudState.currentProcessInstanceValue();
        AtomicReference<RecoveryLease> established = new AtomicReference<>();
        boolean completed = leaseLane.runAndWait(
                () -> established.set(beginProcess(instance)), 1_500L);
        if (!completed || established.get() == null) {
            preserveRecoveryAfterAttachFailure = true;
            stopped = true;
            return false;
        }
        channel = cloudChannel;
        stopped = false;
        return true;
    }

    void submitSignal(String reason) {
        if (stopped) {
            return;
        }
        CloudChannelClient current = channel;
        if (current != null) {
            current.onRecoverySignal(reason == null ? "recovery_signal" : reason);
        }
    }

    void authenticatedProgress() {
        if (!stopped) {
            authenticatedProgressRequested.set(true);
            scheduleCriticalDrain();
        }
    }

    void submitValidatedNetworkAvailability(boolean available) {
        if (stopped) {
            return;
        }
        latestNetworkAvailability.set(available);
        networkAvailabilityRequested.set(available);
        criticalWriteRetryCount.set(0);
        scheduleCriticalDrain();
    }

    void blockTerminal() {
        terminalBlockRequested.set(true);
        criticalWriteRetryCount.set(0);
        scheduleCriticalDrain();
    }

    boolean requestInfrastructureRecovery(String reason) {
        if (stopped || generation < 0L || processInstanceId.isEmpty()) {
            return false;
        }
        if (infrastructureRecoveryConsumed.get()) {
            // One bounded infrastructure lane replacement is allowed per process generation.
            // Keeping the process alive is safer than leaking unbounded blocked Binder threads.
            return true;
        }
        infrastructureRecoveryReason.compareAndSet(null, normalizeInfrastructureReason(reason));
        scheduleInfrastructureRecovery(0);
        return true;
    }

    void resumeGuardianClaim(long expectedGeneration, String expectedProcess,
            String guardianOwner) {
        if (stopped) {
            return;
        }
        try {
            criticalLeaseWriter.execute(() -> {
                if (stopped) {
                    return;
                }
                // A delivered Alarm may race with a later terminal/offline transition. Those
                // desired states outrank recovery even if their first persistence attempt failed.
                if (terminalBlockRequested.get()) {
                    blockLease(RecoveryLease.OWNER_TERMINAL_BLOCKED);
                    return;
                }
                if (Boolean.FALSE.equals(latestNetworkAvailability.get())) {
                    blockLease(RecoveryLease.OWNER_NETWORK_BLOCKED);
                    return;
                }
                long now = SystemClock.elapsedRealtime();
                try {
                    RecoveryLease resumed = store.resumeExactClaim(
                            expectedGeneration,
                            expectedProcess,
                            guardianOwner,
                            now,
                            now + LEASE_WINDOW_MILLIS);
                    if (resumed == null || !resumed.isOwnedByMain() ||
                            resumed.generation != generation ||
                            !resumed.processInstanceId.equals(processInstanceId)) {
                        if (resumed != null && resumed.isGuardianFailureBlocked()) {
                            cancelGuardian();
                        }
                        return;
                    }
                    armGuardian(resumed);
                    CloudChannelClient current = channel;
                    if (current != null) {
                        String trigger = guardianTrigger(guardianOwner);
                        if (trigger.startsWith("infrastructure:")) {
                            infrastructureRecoveryConsumed.set(true);
                            infrastructureRecoveryReason.set(null);
                            current.onInfrastructureRecoverySignal(trigger.substring(
                                    "infrastructure:".length()));
                        } else {
                            current.onRecoverySignal("guardian_lease_claimed");
                            if (infrastructureRecoveryReason.get() != null &&
                                    !infrastructureRecoveryConsumed.get()) {
                                scheduleInfrastructureRecovery(0);
                            }
                        }
                    }
                } catch (IOException | RuntimeException ignored) {
                }
            });
        } catch (RejectedExecutionException stoppedWriter) {
            // stop() owns the exact lease invalidation fallback.
        }
    }

    void stop() {
        stopped = true;
        channel = null;
        if (preserveRecoveryAfterAttachFailure) {
            // attach() timed out after its storage work had already been accepted. Do not
            // interrupt it between beginProcess() and armGuardian(), and do not overwrite the
            // recoverable generation with STOPPED during initialization cleanup.
            leaseLane.stopAfterCurrent();
            criticalLeaseWriter.shutdownNow();
            return;
        }
        boolean cleared = leaseLane.runAndWait(() -> {
            cancelGuardian();
            try {
                if (generation >= 0L && !processInstanceId.isEmpty()) {
                    store.stop(generation, processInstanceId);
                }
            } catch (IOException ignored) {
            }
        }, 1_500L);
        if (!cleared) {
            cancelGuardian();
            try {
                if (generation >= 0L && !processInstanceId.isEmpty()) {
                    store.stop(generation, processInstanceId);
                }
            } catch (IOException ignored) {
            }
        }
        leaseLane.stopNow();
        criticalLeaseWriter.shutdownNow();
    }

    private RecoveryLease beginProcess(String instance) {
        long now = SystemClock.elapsedRealtime();
        try {
            RecoveryLease lease = store.beginProcess(instance, now, now + LEASE_WINDOW_MILLIS);
            generation = lease.generation;
            processInstanceId = lease.processInstanceId;
            cancelGuardian();
            armGuardian(lease);
            if (stopped && !preserveRecoveryAfterAttachFailure) {
                store.stop(lease.generation, lease.processInstanceId);
                cancelGuardian();
                return null;
            }
            return lease;
        } catch (IOException | RuntimeException ignored) {
            // No alarm is safer than an unverified/non-atomic lease. START_STICKY and explicit
            // signals remain available but no timed guarantee is claimed.
            return null;
        }
    }

    private void recordAuthenticatedProgressAndArm() {
        long currentGeneration = generation;
        String currentProcess = processInstanceId;
        if (stopped || currentGeneration < 0L || currentProcess.isEmpty() ||
                terminalBlockRequested.get() ||
                Boolean.FALSE.equals(latestNetworkAvailability.get())) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        try {
            RecoveryLease lease = store.recordAuthenticatedProgress(
                    currentGeneration,
                    currentProcess,
                    now,
                    now + LEASE_WINDOW_MILLIS);
            if (lease != null) {
                armGuardian(lease);
                if (infrastructureRecoveryReason.get() != null &&
                        !infrastructureRecoveryConsumed.get()) {
                    scheduleInfrastructureRecovery(0);
                }
            }
        } catch (IOException | RuntimeException ignored) {
        }
    }

    private void scheduleCriticalDrain() {
        if (stopped || !criticalDrainScheduled.compareAndSet(false, true)) {
            return;
        }
        try {
            criticalLeaseWriter.execute(this::drainCriticalState);
        } catch (RejectedExecutionException stoppedWriter) {
            criticalDrainScheduled.set(false);
        }
    }

    private void drainCriticalState() {
        boolean retryCriticalWrite = false;
        try {
            while (!stopped) {
                boolean terminal = terminalBlockRequested.get();
                Boolean networkAvailable = networkAvailabilityRequested.getAndSet(null);
                boolean authenticatedProgress = authenticatedProgressRequested.getAndSet(false);
                if (terminal) {
                    boolean applied = blockLease(RecoveryLease.OWNER_TERMINAL_BLOCKED);
                    terminalBlockApplied.set(applied);
                    retryCriticalWrite = !applied;
                    if (applied) {
                        criticalWriteRetryCount.set(0);
                    }
                    networkAvailabilityRequested.set(null);
                    return;
                }
                if (networkAvailable == null) {
                    if (authenticatedProgress) {
                        recordAuthenticatedProgressAndArm();
                    }
                    return;
                }
                boolean networkUpdated = updateNetworkLease(networkAvailable);
                if (!networkUpdated && !networkAvailable) {
                    // Preserve a newer availability signal if one arrived while this write ran.
                    networkAvailabilityRequested.compareAndSet(null, false);
                    retryCriticalWrite = true;
                } else {
                    criticalWriteRetryCount.set(0);
                }
                if (authenticatedProgress && networkUpdated) {
                    recordAuthenticatedProgressAndArm();
                }
            }
        } finally {
            criticalDrainScheduled.set(false);
            if (!stopped && retryCriticalWrite) {
                scheduleCriticalRetry();
            } else if (!stopped && ((terminalBlockRequested.get() &&
                    !terminalBlockApplied.get()) ||
                    (!terminalBlockRequested.get() &&
                            (networkAvailabilityRequested.get() != null ||
                                    authenticatedProgressRequested.get())))) {
                scheduleCriticalDrain();
            }
        }
    }

    private boolean updateNetworkLease(boolean available) {
        if (!available) {
            return blockLease(RecoveryLease.OWNER_NETWORK_BLOCKED);
        }
        long now = SystemClock.elapsedRealtime();
        try {
            RecoveryLease resumed = store.resumeBlocked(
                    generation, processInstanceId, RecoveryLease.OWNER_NETWORK_BLOCKED,
                    now, now + LEASE_WINDOW_MILLIS);
            if (resumed != null) {
                armGuardian(resumed);
            }
            if (infrastructureRecoveryReason.get() != null &&
                    !infrastructureRecoveryConsumed.get()) {
                scheduleInfrastructureRecovery(0);
            }
            // Null means this generation was already MAIN, terminal, stopped or stale. All are
            // authoritative outcomes; only storage failure needs a retry.
            return true;
        } catch (IOException | RuntimeException ignored) {
            return false;
        }
    }

    private void scheduleCriticalRetry() {
        int attempt = criticalWriteRetryCount.incrementAndGet();
        if (attempt > MAX_CRITICAL_WRITE_RETRIES || stopped) {
            return;
        }
        long delayMillis = 100L << (attempt - 1);
        try {
            criticalLeaseWriter.schedule(this::scheduleCriticalDrain,
                    delayMillis, TimeUnit.MILLISECONDS);
        } catch (RejectedExecutionException stoppedWriter) {
            // stop() owns the final exact lease invalidation fallback.
        }
    }

    private boolean blockLease(String owner) {
        // Cancel the one-shot alarm first. If storage is unavailable, leaving the current lease
        // untouched but unarmed is fail-closed: no stale guardian can claim it.
        cancelGuardian();
        try {
            return store.blockGeneration(generation, processInstanceId, owner) != null;
        } catch (IOException | RuntimeException ignored) {
            return false;
        }
    }

    private void scheduleInfrastructureRecovery(int retryAttempt) {
        if (stopped || infrastructureRecoveryConsumed.get() ||
                infrastructureRecoveryReason.get() == null ||
                terminalBlockRequested.get() ||
                Boolean.FALSE.equals(latestNetworkAvailability.get()) ||
                !infrastructureRecoveryScheduled.compareAndSet(false, true)) {
            return;
        }
        try {
            criticalLeaseWriter.execute(() -> {
                infrastructureRecoveryScheduled.set(false);
                if (stopped || infrastructureRecoveryConsumed.get() ||
                        terminalBlockRequested.get() ||
                        Boolean.FALSE.equals(latestNetworkAvailability.get())) {
                    return;
                }
                String reason = infrastructureRecoveryReason.get();
                if (reason == null) {
                    return;
                }
                long now = SystemClock.elapsedRealtime();
                String requestOwner = RecoveryLease.OWNER_MAIN_RECOVERY_PREFIX +
                        "infrastructure:" + reason;
                try {
                    RecoveryLease requested = store.requestImmediateRecovery(
                            generation, processInstanceId, now, requestOwner);
                    if (requested == null) {
                        RecoveryLease current = store.read();
                        if (current == null || !current.belongsTo(generation, processInstanceId) ||
                                !requestOwner.equals(current.recoveryOwner)) {
                            return;
                        }
                        requested = current;
                    }
                    armGuardian(requested, "infrastructure:" + reason);
                    // The bounded infrastructure rescue is consumed once the system request is
                    // armed. An exact guardian ACK may still create the lane, but timeout/reject
                    // must not be converted into an alarm loop by later authenticated progress.
                    infrastructureRecoveryConsumed.set(true);
                } catch (IOException | RuntimeException unavailable) {
                    if (retryAttempt < MAX_CRITICAL_WRITE_RETRIES) {
                        try {
                            criticalLeaseWriter.schedule(
                                    () -> scheduleInfrastructureRecovery(retryAttempt + 1),
                                    100L << retryAttempt,
                                    TimeUnit.MILLISECONDS);
                        } catch (RejectedExecutionException ignored) {
                        }
                    } else {
                        try {
                            store.failImmediateRecovery(
                                    generation, processInstanceId, requestOwner);
                        } catch (IOException ignored) {
                        }
                    }
                }
            });
        } catch (RejectedExecutionException stoppedWriter) {
            infrastructureRecoveryScheduled.set(false);
            return;
        }
    }

    private void armGuardian(RecoveryLease lease) {
        armGuardian(lease, "liveness");
    }

    private void armGuardian(RecoveryLease lease, String trigger) {
        AlarmManager alarms = context.getSystemService(AlarmManager.class);
        if (alarms == null) {
            return;
        }
        alarms.setAndAllowWhileIdle(
                AlarmManager.ELAPSED_REALTIME_WAKEUP,
                lease.deadlineElapsed,
                guardianIntent(context, lease, newGuardianOwner(trigger), "liveness", 0,
                        PendingIntent.FLAG_UPDATE_CURRENT));
    }

    private void cancelGuardian() {
        try {
            AlarmManager alarms = context.getSystemService(AlarmManager.class);
            if (alarms == null) {
                return;
            }
            PendingIntent existing = guardianIntent(
                    context,
                    new RecoveryLease(generation, processInstanceId, 0L, 0L, ""),
                    "",
                    "liveness",
                    0,
                    PendingIntent.FLAG_NO_CREATE);
            if (existing != null) {
                alarms.cancel(existing);
                existing.cancel();
            }
        } catch (RuntimeException ignored) {
            // The persistent owner transition remains the second, authoritative fence. A
            // delivered stale PendingIntent cannot claim a non-main or mismatched generation.
        }
    }

    static PendingIntent guardianIntent(
            Context context,
            RecoveryLease lease,
            String owner,
            String phase,
            int preclaimRetries,
            int updateFlag
    ) {
        Intent intent = guardianBroadcast(context, lease, owner, phase, preclaimRetries);
        return PendingIntent.getBroadcast(
                context,
                GUARDIAN_REQUEST_CODE,
                intent,
                updateFlag | PendingIntent.FLAG_IMMUTABLE);
    }

    private static Intent guardianBroadcast(
            Context context,
            RecoveryLease lease,
            String owner,
            String phase,
            int preclaimRetries
    ) {
        return new Intent(context, RecoveryGuardianReceiver.class)
                .setAction(ACTION_GUARDIAN)
                .setData(Uri.parse(RecoveryAlarmIdentity.dataUri(
                        lease.generation, lease.processInstanceId, phase)))
                .putExtra(EXTRA_GENERATION, lease.generation)
                .putExtra(EXTRA_PROCESS_INSTANCE_ID, lease.processInstanceId)
                .putExtra(EXTRA_GUARDIAN_OWNER, owner)
                .putExtra(EXTRA_GUARDIAN_PRECLAIM_RETRIES, preclaimRetries);
    }

    private static String newGuardianOwner(String trigger) {
        return RecoveryLease.OWNER_GUARDIAN_PENDING_PREFIX + safeTrigger(trigger) + ":" +
                UUID.randomUUID();
    }

    private static String guardianTrigger(String owner) {
        if (owner == null || !owner.startsWith(RecoveryLease.OWNER_GUARDIAN_PENDING_PREFIX)) {
            return "liveness";
        }
        String remainder = owner.substring(RecoveryLease.OWNER_GUARDIAN_PENDING_PREFIX.length());
        int tokenSeparator = remainder.lastIndexOf(':');
        return tokenSeparator <= 0 ? "liveness" : remainder.substring(0, tokenSeparator);
    }

    private static String normalizeInfrastructureReason(String reason) {
        if ("network_io_rotation_exhausted".equals(reason)) {
            return reason;
        }
        return "identity_io_rotation_exhausted";
    }

    private static String safeTrigger(String trigger) {
        if (trigger == null || trigger.isEmpty()) {
            return "liveness";
        }
        return trigger.replaceAll("[^a-z0-9_:]", "_");
    }
}
