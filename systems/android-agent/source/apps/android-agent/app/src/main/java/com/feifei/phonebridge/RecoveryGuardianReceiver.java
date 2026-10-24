package com.feifei.phonebridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;
import android.app.AlarmManager;
import android.app.PendingIntent;

import java.io.File;
import java.io.IOException;

/** One-shot out-of-process lease claimant. It never creates or owns cloud transport. */
public final class RecoveryGuardianReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !RecoveryCoordinator.ACTION_GUARDIAN.equals(intent.getAction())) {
            return;
        }
        final PendingResult pending = goAsync();
        Context app = context.getApplicationContext();
        Intent copied = new Intent(intent);
        Thread worker = new Thread(() -> {
            try {
                handleOnce(app, copied);
            } finally {
                pending.finish();
            }
        }, "oin-recovery-guardian-once");
        worker.setDaemon(true);
        worker.start();
    }

    private static void handleOnce(Context context, Intent intent) {
        long generation = intent.getLongExtra(RecoveryCoordinator.EXTRA_GENERATION, -1L);
        String processInstanceId = intent.getStringExtra(
                RecoveryCoordinator.EXTRA_PROCESS_INSTANCE_ID);
        String guardianOwner = intent.getStringExtra(RecoveryCoordinator.EXTRA_GUARDIAN_OWNER);
        int preclaimRetries = intent.getIntExtra(
                RecoveryCoordinator.EXTRA_GUARDIAN_PRECLAIM_RETRIES, 0);
        if (guardianOwner == null ||
                !guardianOwner.startsWith(RecoveryLease.OWNER_GUARDIAN_PENDING_PREFIX)) {
            return;
        }
        RecoveryLeaseStore store = new RecoveryLeaseStore(
                new File(context.getNoBackupFilesDir(), "oin-recovery"));
        RecoveryLease claim = null;
        try {
            long now = SystemClock.elapsedRealtime();
            claim = store.claimExpired(
                    generation,
                    processInstanceId,
                    now,
                    now + RecoveryCoordinator.HANDOFF_WINDOW_MILLIS,
                    guardianOwner);
            if (claim == null) {
                RecoveryLease timedOut = store.expireExactGuardianHandoff(
                        generation, processInstanceId, guardianOwner, now);
                if (timedOut != null) {
                    cancelExactAlarm(context, timedOut, guardianOwner);
                }
                return;
            }
            // A distinct one-shot deadline judges Service handoff. The API return below is only
            // request acceptance; exact onStartCommand acknowledgement is the recovery handoff.
            armExactAlarm(context, claim, guardianOwner, preclaimRetries);
            ControlService.GuardianRecoveryStartResult result =
                    ControlService.requestGuardianRecovery(
                            context, claim.generation, claim.processInstanceId, guardianOwner);
            if (result != ControlService.GuardianRecoveryStartResult.REQUESTED) {
                RecoveryLease rejected = store.failExactGuardianHandoff(
                        claim.generation,
                        claim.processInstanceId,
                        guardianOwner,
                        RecoveryLease.OWNER_GUARDIAN_START_REJECTED);
                if (rejected != null) {
                    cancelExactAlarm(context, claim, guardianOwner);
                }
            }
        } catch (IOException | RuntimeException ignored) {
            // Never release to an already-expired MAIN lease. Persist a bounded, observable
            // guardian failure when the exact claim is still ours.
            if (claim != null) {
                RecoveryLease failed = null;
                try {
                    failed = store.failExactGuardianHandoff(
                            claim.generation,
                            claim.processInstanceId,
                            guardianOwner,
                            RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE);
                } catch (IOException ignoredRelease) {
                }
                if (failed != null) {
                    cancelExactAlarm(context, claim, guardianOwner);
                }
            } else {
                // claimExpired may fail before ownership changes. The one-shot Alarm has already
                // fired, so a later best-effort transaction records an explicit blocked state
                // instead of leaving an expired MAIN lease with no pending system trigger.
                if (RecoveryGuardianRetryPolicy.mayRetry(preclaimRetries)) {
                    armPreclaimRetry(
                            context,
                            generation,
                            processInstanceId,
                            guardianOwner,
                            preclaimRetries + 1,
                            SystemClock.elapsedRealtime() +
                                    RecoveryGuardianRetryPolicy.delayMillis(preclaimRetries));
                } else {
                    try {
                        store.blockExpiredMainAfterGuardianFailure(
                                generation,
                                processInstanceId,
                                SystemClock.elapsedRealtime(),
                                RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE);
                    } catch (IOException ignoredBlock) {
                        // Bounded retries are exhausted and atomic storage is still unavailable.
                        // Boot/sticky/user/push are the remaining legitimate sources.
                    }
                }
            }
        }
    }

    private static void armExactAlarm(
            Context context,
            RecoveryLease lease,
            String owner,
            int preclaimRetries
    ) {
        AlarmManager alarms = context.getSystemService(AlarmManager.class);
        if (alarms == null) {
            throw new IllegalStateException("guardian_alarm_manager_unavailable");
        }
        alarms.setAndAllowWhileIdle(
                AlarmManager.ELAPSED_REALTIME_WAKEUP,
                lease.deadlineElapsed,
                RecoveryCoordinator.guardianIntent(
                        context, lease, owner, "handoff", preclaimRetries,
                        PendingIntent.FLAG_UPDATE_CURRENT));
    }

    private static void armPreclaimRetry(
            Context context,
            long generation,
            String processInstanceId,
            String owner,
            int retries,
            long triggerElapsed
    ) {
        AlarmManager alarms = context.getSystemService(AlarmManager.class);
        if (alarms == null) {
            return;
        }
        RecoveryLease retry = new RecoveryLease(
                generation, processInstanceId, 0L, triggerElapsed, RecoveryLease.OWNER_MAIN);
        alarms.setAndAllowWhileIdle(
                AlarmManager.ELAPSED_REALTIME_WAKEUP,
                triggerElapsed,
                RecoveryCoordinator.guardianIntent(
                        context, retry, owner, "preclaim", retries,
                        PendingIntent.FLAG_UPDATE_CURRENT));
    }

    private static void cancelExactAlarm(Context context, RecoveryLease lease, String owner) {
        try {
            AlarmManager alarms = context.getSystemService(AlarmManager.class);
            if (alarms == null) {
                return;
            }
            PendingIntent existing = RecoveryCoordinator.guardianIntent(
                    context, lease, owner, "handoff", 0, PendingIntent.FLAG_NO_CREATE);
            if (existing != null) {
                alarms.cancel(existing);
                existing.cancel();
            }
        } catch (RuntimeException ignored) {
            // The persisted owner/generation is authoritative if a stale PendingIntent arrives.
        }
    }

}
