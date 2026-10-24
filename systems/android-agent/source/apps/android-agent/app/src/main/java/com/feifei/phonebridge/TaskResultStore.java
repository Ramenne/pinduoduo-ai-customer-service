package com.feifei.phonebridge;

import android.content.ContentValues;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.DatabaseErrorHandler;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteDatabaseCorruptException;
import android.database.sqlite.SQLiteDiskIOException;
import android.database.sqlite.SQLiteException;
import android.database.sqlite.SQLiteFullException;
import android.database.sqlite.SQLiteOpenHelper;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Device-scoped, transactional task result outbox. */
final class TaskResultStore {
    static final String READY = "ready";
    static final String RECOVERY_PENDING = "recovery_pending";
    static final String UNAVAILABLE = "unavailable";
    static final String NOT_BOUND = "not_bound";

    static final int MAX_RESULT_BYTES = 900 * 1024;
    static final int MAX_TOTAL_RESULT_BYTES = 8 * 1024 * 1024;
    static final int MAX_IN_FLIGHT = 8;
    static final int MAX_COMPLETED = 256;
    private static final int MAX_GLOBAL_COMPLETED = 1024;
    private static final int MAX_TOTAL_ROWS = 4096;

    private static final String DATABASE_NAME = "oin_task_result_outbox.db";
    private static final int DATABASE_VERSION = 2;
    private static final String TASKS = "task_results";
    private static final String META = "store_meta";
    private static final String QUARANTINE = "legacy_quarantine";
    private static final String LEGACY_PREFS = "oin_task_result_outbox";
    private static final String LEGACY_LEDGER = "ledger";
    private static final String LEGACY_SCOPE = "device_scope";
    private static final String META_MIGRATION = "legacy_migration";
    private static final String META_MIGRATION_COMPLETE = "complete";
    private static final String META_MIGRATION_RECOVERY_PENDING = "recovery_pending";
    private static final String META_RECOVERY_SAFE_AFTER = "recovery_safe_after";
    private static final String META_RECONCILIATION_COMPLETE_ID = "reconciliation_complete_id";
    private static final String META_RECONCILIATION_COMPLETE_OLD_SCOPE =
            "reconciliation_complete_old_scope";
    private static final String META_RECONCILIATION_COMPLETE_NEW_SCOPE =
            "reconciliation_complete_new_scope";
    private static final Object LEGACY_MIGRATION_LOCK = new Object();
    private static final ActivePublisherGate PUBLISHER_GATE = new ActivePublisherGate();
    private static TaskResultStore processInstance;

    private static final String STATE_RUNNING = "RUNNING";
    private static final String STATE_PENDING = "PENDING";
    private static final String STATE_COMPLETED = "COMPLETED";

    static final class ReconciliationRecord {
        final String taskId;
        final String localState;
        final String resultMessage;
        final String resultSha256;
        final int resultBytes;

        ReconciliationRecord(
                String taskId,
                String localState,
                String resultMessage,
                String resultSha256,
                int resultBytes
        ) {
            this.taskId = taskId;
            this.localState = localState;
            this.resultMessage = resultMessage;
            this.resultSha256 = resultSha256;
            this.resultBytes = resultBytes;
        }
    }

    static final class StoreException extends RuntimeException {
        final String code;
        final boolean storageUnavailable;

        StoreException(String code, Throwable cause) {
            this(code, cause, true);
        }

        StoreException(String code, Throwable cause, boolean storageUnavailable) {
            super(code, cause);
            this.code = code;
            this.storageUnavailable = storageUnavailable;
        }

        StoreException(String code) {
            this(code, null);
        }

        static StoreException transition(String code) {
            return new StoreException(code, null, false);
        }
    }

    private final Context context;
    private final DatabaseHelper helper;
    private volatile SharedPreferences legacyPreferences;
    private final ActivePublisherGate.Token publisherToken;
    private SQLiteDatabase database;
    private boolean databaseAvailable;
    private String state = UNAVAILABLE;
    private String diagnosticCode = "task_store_not_initialized";
    private String recoveredScope = "";
    private String readyScope = "";

    static synchronized TaskResultStore getProcessInstance(Context context) {
        if (processInstance == null) {
            processInstance = new TaskResultStore(context);
        }
        return processInstance;
    }

    private TaskResultStore(Context context) {
        this.context = context.getApplicationContext();
        this.helper = new DatabaseHelper(this.context);
        this.publisherToken = PUBLISHER_GATE.activate();
    }

    private SharedPreferences legacyPreferences() {
        SharedPreferences current = legacyPreferences;
        if (current != null) {
            return current;
        }
        synchronized (this) {
            if (legacyPreferences == null) {
                legacyPreferences = context.getSharedPreferences(
                        LEGACY_PREFS,
                        Context.MODE_PRIVATE
                );
            }
            return legacyPreferences;
        }
    }

    synchronized void prepareForCurrentScope() {
        try {
            if (!databaseAvailable) {
                database = helper.getWritableDatabase();
                migrateLegacyIfNeeded(database);
                databaseAvailable = true;
            }
            String scope = CloudState.deviceId(context);
            if (scope.isEmpty()) {
                publish(NOT_BOUND, "", 0);
            } else if (hasUnresolvedQuarantine(database)) {
                publish(RECOVERY_PENDING, quarantineClassification(database), 0);
            } else if (!OutboxReconciliationRules.channelReady(
                    hasForeignNonterminal(database, scope)
            )) {
                publish(RECOVERY_PENDING, "foreign_scope_nonterminal", 0);
            } else {
                if (TaskStoreRules.shouldRecoverOnPrepare(recoveredScope, scope)) {
                    recoverInterruptedRows(database, scope);
                    recoveredScope = scope;
                }
                publishReady(scope, cleanupWarning(), countPending(database, scope));
            }
        } catch (RuntimeException failure) {
            markUnavailable(failure);
        }
    }

    synchronized boolean isTaskChannelReady() {
        String currentScope = CloudState.deviceId(context);
        return publisherToken.isActive() && databaseAvailable && READY.equals(state) &&
                !currentScope.isEmpty() &&
                currentScope.equals(readyScope);
    }

    synchronized boolean isRecoveryPending() {
        return databaseAvailable && RECOVERY_PENDING.equals(state);
    }

    synchronized String state() {
        return state;
    }

    synchronized String diagnosticCode() {
        return diagnosticCode;
    }

    synchronized long recoverySafeAfter() {
        if (!databaseAvailable) {
            return 0L;
        }
        String value = metaValue(database, META_RECOVERY_SAFE_AFTER);
        try {
            return value == null ? 0L : Long.parseLong(value);
        } catch (NumberFormatException invalid) {
            return 0L;
        }
    }

    synchronized void recordRecoverySafeAfter(long safeAfter) {
        if (!databaseAvailable || !isRecoveryPending() || safeAfter <= 0L) {
            return;
        }
        try {
            long negotiated;
            database.beginTransaction();
            try {
                String stored = metaValue(database, META_RECOVERY_SAFE_AFTER);
                long existing;
                try {
                    existing = stored == null ? 0L : Long.parseLong(stored);
                } catch (NumberFormatException invalid) {
                    throw new StoreException("task_store_recovery_safe_after_corrupt", invalid);
                }
                negotiated = Math.max(existing, safeAfter);
                putMeta(database, META_RECOVERY_SAFE_AFTER, Long.toString(negotiated));
                database.setTransactionSuccessful();
            } finally {
                database.endTransaction();
            }
            if (publisherToken.isActive()) {
                CloudState.setTaskRecoverySafeAfter(context, negotiated);
            }
        } catch (RuntimeException failure) {
            markUnavailable(failure);
        }
    }

    synchronized boolean completeRecovery(long trustedServerNow) {
        if (!databaseAvailable || !isRecoveryPending() || trustedServerNow <= 0L ||
                CloudState.deviceId(context).isEmpty()) {
            return isTaskChannelReady();
        }
        try {
            String scope;
            long safeAfter;
            String blockedCode = "";
            synchronized (CloudState.class) {
                scope = CloudState.deviceId(context);
                if (scope.isEmpty()) {
                    return false;
                }
                database.beginTransaction();
                try {
                    String storedSafeAfter = metaValue(database, META_RECOVERY_SAFE_AFTER);
                    try {
                        safeAfter = storedSafeAfter == null ? 0L : Long.parseLong(storedSafeAfter);
                    } catch (NumberFormatException invalid) {
                        throw new StoreException("task_store_recovery_safe_after_corrupt", invalid);
                    }
                    if (!TaskStoreRules.recoveryTimeReached(safeAfter, trustedServerNow)) {
                        database.setTransactionSuccessful();
                        return false;
                    }
                    if (!OutboxReconciliationRules.channelReady(
                            hasForeignNonterminal(database, scope)
                    )) {
                        blockedCode = "foreign_scope_nonterminal";
                    } else {
                        int unresolvedCount = unresolvedQuarantineCount(database);
                        if (unresolvedCount <= 0) {
                            if (!META_MIGRATION_COMPLETE.equals(
                                    metaValue(database, META_MIGRATION)
                            )) {
                                blockedCode = "legacy_scope_mismatch";
                            }
                        } else if (!unresolvedQuarantineMatchesScope(database, scope)) {
                            blockedCode = "legacy_scope_mismatch";
                        } else {
                            ContentValues resolved = new ContentValues();
                            resolved.put("resolved_at", System.currentTimeMillis());
                            resolved.put("safe_after", safeAfter);
                            if (database.update(
                                    QUARANTINE,
                                    resolved,
                                    "resolved_at IS NULL",
                                    null
                            ) != unresolvedCount) {
                                throw new StoreException("task_store_quarantine_resolution_failed");
                            }
                            putMeta(database, META_MIGRATION, META_MIGRATION_COMPLETE);
                            putMeta(database, META_RECOVERY_SAFE_AFTER, Long.toString(safeAfter));
                        }
                    }
                    database.setTransactionSuccessful();
                } finally {
                    database.endTransaction();
                }
            }
            if (!blockedCode.isEmpty()) {
                publish(RECOVERY_PENDING, blockedCode, 0);
                return false;
            }
            boolean cleaned = legacyPreferences().edit().clear().commit();
            diagnosticCode = cleaned ? "" : "legacy_outbox_cleanup_failed";
            recoverInterruptedRows(database, scope);
            recoveredScope = scope;
            publishReady(scope, diagnosticCode, countPending(database, scope));
            AuditLog.append(context, "task_store_recovery_completed", true, new JSONObject()
                    .put("safe_after", safeAfter)
                    .put("legacy_cleanup", cleaned));
            return true;
        } catch (Exception failure) {
            markUnavailable(failure);
            return false;
        }
    }

    synchronized void recoverInterrupted() {
        String currentScope = requireScope();
        SQLiteDatabase db = requireReadyDatabase(currentScope);
        try {
            recoverInterruptedRows(db, currentScope);
            recoveredScope = currentScope;
            publishCurrentReadyStatus(db);
        } catch (RuntimeException failure) {
            throw storageFailure(failure);
        }
    }

    synchronized TaskResultLedger.Claim claim(
            String taskId,
            TaskAdmissionPermit permit
    ) {
        synchronized (CloudState.class) {
            return claimForStableBinding(taskId, permit);
        }
    }

    private TaskResultLedger.Claim claimForStableBinding(
            String taskId,
            TaskAdmissionPermit permit
    ) {
        taskId = requireTaskId(taskId);
        String scope = requireScope();
        SQLiteDatabase db = requireReadyDatabase(scope);
        try {
            TaskResultLedger.Claim outcome;
            boolean changed = false;
            db.beginTransaction();
            try {
                requireCurrentReadyScope(db, scope);
                StoredRecord existing = find(db, scope, taskId);
                if (existing != null) {
                    switch (existing.state) {
                        case STATE_PENDING:
                            outcome = new TaskResultLedger.Claim(
                                    TaskResultLedger.ClaimKind.PENDING,
                                    existing.resultMessage,
                                    scope
                            );
                            break;
                        case STATE_RUNNING:
                            outcome = new TaskResultLedger.Claim(
                                    TaskResultLedger.ClaimKind.RUNNING,
                                    null,
                                    scope
                            );
                            break;
                        case STATE_COMPLETED:
                            outcome = new TaskResultLedger.Claim(
                                    TaskResultLedger.ClaimKind.COMPLETED,
                                    null,
                                    scope
                            );
                            break;
                        default:
                            throw new StoreException("task_store_invalid_row_state");
                    }
                } else if (!TaskStoreRules.canClaim(
                        countInFlight(db, scope),
                        countInFlightAllScopes(db),
                        countRows(db),
                        reservedResultBytes(db),
                        MAX_IN_FLIGHT,
                        MAX_TOTAL_ROWS,
                        MAX_TOTAL_RESULT_BYTES,
                        MAX_RESULT_BYTES
                )) {
                    outcome = new TaskResultLedger.Claim(
                            TaskResultLedger.ClaimKind.CAPACITY,
                            null,
                            scope
                    );
                } else {
                    if (permit == null || !permit.isValid()) {
                        String expired = failureMessage(
                                taskId, "task_admission_deadline_exceeded");
                        ContentValues values = new ContentValues();
                        values.put("device_scope", scope);
                        values.put("task_id", taskId);
                        values.put("state", STATE_PENDING);
                        values.put("result_message", expired);
                        values.put("result_bytes", utf8Bytes(expired));
                        values.put("updated_at", System.currentTimeMillis());
                        if (db.insertOrThrow(TASKS, null, values) < 0L) {
                            throw new StoreException("task_store_insert_failed");
                        }
                        changed = true;
                        outcome = new TaskResultLedger.Claim(
                                TaskResultLedger.ClaimKind.DEADLINE_EXPIRED,
                                expired,
                                scope
                        );
                    } else {
                    ContentValues values = new ContentValues();
                    values.put("device_scope", scope);
                    values.put("task_id", taskId);
                    values.put("state", STATE_RUNNING);
                    values.putNull("result_message");
                    values.put("result_bytes", 0);
                    values.put("updated_at", System.currentTimeMillis());
                    if (db.insertOrThrow(TASKS, null, values) < 0L) {
                        throw new StoreException("task_store_insert_failed");
                    }
                    changed = true;
                    outcome = new TaskResultLedger.Claim(
                            TaskResultLedger.ClaimKind.NEW,
                            null,
                            scope
                    );
                    }
                }
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
            if (changed) {
                publishCurrentReadyStatus(db);
            }
            return outcome;
        } catch (RuntimeException failure) {
            throw storageFailure(failure);
        }
    }

    synchronized String persistResult(String taskScope, String taskId, String resultMessage) {
        taskId = requireTaskId(taskId);
        String scope = TaskStoreRules.sanitizeDeviceScope(taskScope);
        if (scope.isEmpty()) {
            throw StoreException.transition("task_result_scope_invalid");
        }
        SQLiteDatabase db = requireAvailableDatabase();
        try {
            String outcome;
            boolean changed = false;
            db.beginTransaction();
            try {
                StoredRecord existing = find(db, scope, taskId);
                if (existing == null || !STATE_RUNNING.equals(existing.state) &&
                        !STATE_PENDING.equals(existing.state)) {
                    throw StoreException.transition("task_not_running");
                }
                if (STATE_PENDING.equals(existing.state)) {
                    outcome = existing.resultMessage;
                } else {
                    String stored = normalizeResultMessage(taskId, resultMessage);
                    int storedBytes = utf8Bytes(stored);
                    int existingTotal = totalResultBytes(db);
                    if (existingTotal + storedBytes > MAX_TOTAL_RESULT_BYTES) {
                        stored = failureMessage(taskId, "task_result_outbox_total_limit");
                        storedBytes = utf8Bytes(stored);
                    }
                    if (existingTotal + storedBytes > MAX_TOTAL_RESULT_BYTES) {
                        throw new StoreException("task_store_total_limit_invariant_broken");
                    }
                    ContentValues values = new ContentValues();
                    values.put("state", STATE_PENDING);
                    values.put("result_message", stored);
                    values.put("result_bytes", storedBytes);
                    values.put("updated_at", System.currentTimeMillis());
                    if (db.update(
                            TASKS,
                            values,
                            "device_scope=? AND task_id=?",
                            new String[]{scope, taskId}
                    ) != 1) {
                        throw new StoreException("task_store_update_failed");
                    }
                    changed = true;
                    outcome = stored;
                }
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
            if (changed) {
                prepareForCurrentScope();
            }
            return outcome;
        } catch (RuntimeException failure) {
            throw storageFailure(failure);
        }
    }

    synchronized void acknowledge(String taskId) {
        taskId = requireTaskId(taskId);
        String scope = requireScope();
        SQLiteDatabase db = requireReadyDatabase(scope);
        try {
            db.beginTransaction();
            try {
                StoredRecord existing = find(db, scope, taskId);
                if (existing == null) {
                    db.setTransactionSuccessful();
                    return;
                }
                if (STATE_PENDING.equals(existing.state)) {
                    ContentValues values = new ContentValues();
                    values.put("state", STATE_COMPLETED);
                    values.putNull("result_message");
                    values.put("result_bytes", 0);
                    values.put("updated_at", System.currentTimeMillis());
                    if (db.update(
                            TASKS,
                            values,
                            "device_scope=? AND task_id=?",
                            new String[]{scope, taskId}
                    ) != 1) {
                        throw new StoreException("task_store_ack_update_failed");
                    }
                } else if (!STATE_COMPLETED.equals(existing.state)) {
                    throw StoreException.transition("task_result_ack_before_result");
                }
                trimCompleted(db, scope);
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
            publishCurrentReadyStatus(db);
        } catch (RuntimeException failure) {
            throw storageFailure(failure);
        }
    }

    synchronized List<String> pendingMessages() {
        String scope = requireScope();
        SQLiteDatabase db = requireReadyDatabase(scope);
        try (Cursor cursor = db.query(
                TASKS,
                new String[]{"result_message"},
                "device_scope=? AND state=?",
                new String[]{scope, STATE_PENDING},
                null,
                null,
                "updated_at ASC"
        )) {
            List<String> messages = new ArrayList<>();
            while (cursor.moveToNext()) {
                messages.add(cursor.getString(0));
            }
            return messages;
        } catch (RuntimeException failure) {
            throw storageFailure(failure);
        }
    }

    synchronized List<ReconciliationRecord> foreignNonterminalRecords(
            String oldScope,
            String newScope
    ) {
        oldScope = TaskStoreRules.sanitizeDeviceScope(oldScope);
        newScope = TaskStoreRules.sanitizeDeviceScope(newScope);
        if (oldScope.isEmpty() || newScope.isEmpty() || oldScope.equals(newScope) ||
                !newScope.equals(CloudState.deviceId(context))) {
            throw StoreException.transition("reconciliation_scope_mismatch");
        }
        SQLiteDatabase db = requireAvailableDatabase();
        try (Cursor cursor = db.query(
                TASKS,
                new String[]{"task_id", "state", "result_message"},
                "device_scope=? AND state IN (?,?)",
                new String[]{oldScope, STATE_RUNNING, STATE_PENDING},
                null,
                null,
                "updated_at ASC,task_id ASC"
        )) {
            List<ReconciliationRecord> records = new ArrayList<>();
            while (cursor.moveToNext()) {
                if (records.size() >= MAX_IN_FLIGHT) {
                    throw StoreException.transition("reconciliation_manifest_too_large");
                }
                String taskId = cursor.getString(0);
                String localState = cursor.getString(1);
                String resultMessage = cursor.isNull(2) ? null : cursor.getString(2);
                if (STATE_PENDING.equals(localState)) {
                    try {
                        OutboxReconciliationRules.ResultSummary summary =
                                OutboxReconciliationRules.summarize(
                                        taskId,
                                        CanonicalJson.toMap(new JSONObject(resultMessage))
                                );
                        records.add(new ReconciliationRecord(
                                taskId,
                                localState,
                                resultMessage,
                                summary.sha256,
                                summary.byteSize
                        ));
                    } catch (Exception invalid) {
                        throw StoreException.transition("foreign_scope_result_invalid");
                    }
                } else if (STATE_RUNNING.equals(localState)) {
                    records.add(new ReconciliationRecord(taskId, localState, null, "", 0));
                } else {
                    throw StoreException.transition("foreign_scope_state_invalid");
                }
            }
            return records;
        } catch (RuntimeException failure) {
            throw storageFailure(failure);
        }
    }

    synchronized void archiveReconciled(
            String authorizationId,
            String oldScope,
            String newScope,
            String taskId,
            String localState,
            String resultSha256,
            int resultBytes,
            String disposition
    ) {
        authorizationId = requireProtocolId(authorizationId, "reconciliation_authorization_invalid");
        oldScope = TaskStoreRules.sanitizeDeviceScope(oldScope);
        newScope = TaskStoreRules.sanitizeDeviceScope(newScope);
        taskId = requireTaskId(taskId);
        if (oldScope.isEmpty() || newScope.isEmpty() || oldScope.equals(newScope) ||
                !newScope.equals(CloudState.deviceId(context)) ||
                !(STATE_PENDING.equals(localState) || STATE_RUNNING.equals(localState)) ||
                resultSha256 == null ||
                !OutboxReconciliationRules.validDisposition(disposition)) {
            throw StoreException.transition("reconciliation_ack_invalid");
        }
        if (STATE_RUNNING.equals(localState) &&
                (!resultSha256.isEmpty() || resultBytes != 0 ||
                        !"admin_revoked_interrupted".equals(disposition))) {
            throw StoreException.transition("reconciliation_running_ack_invalid");
        }
        SQLiteDatabase db = requireAvailableDatabase();
        try {
            db.beginTransaction();
            try {
                StoredRecord existing = find(db, oldScope, taskId);
                if (existing == null) {
                    throw StoreException.transition("reconciliation_local_task_missing");
                }
                if (STATE_COMPLETED.equals(existing.state)) {
                    if (!authorizationId.equals(existing.reconciliationAuthorizationId) ||
                            !disposition.equals(existing.archiveDisposition) ||
                            !resultSha256.equals(existing.resultSha256) ||
                            resultBytes != existing.originalResultBytes) {
                        throw StoreException.transition("reconciliation_archive_conflict");
                    }
                } else {
                    if (!localState.equals(existing.state)) {
                        throw StoreException.transition("reconciliation_local_state_conflict");
                    }
                    if (STATE_PENDING.equals(localState)) {
                        try {
                            OutboxReconciliationRules.ResultSummary summary =
                                    OutboxReconciliationRules.summarize(
                                            taskId,
                                            CanonicalJson.toMap(
                                                    new JSONObject(existing.resultMessage)
                                            )
                                    );
                            if (!summary.sha256.equals(resultSha256) ||
                                    summary.byteSize != resultBytes ||
                                    "admin_revoked_interrupted".equals(disposition)) {
                                throw StoreException.transition(
                                        "reconciliation_result_ack_mismatch"
                                );
                            }
                        } catch (StoreException invalid) {
                            throw invalid;
                        } catch (Exception invalid) {
                            throw StoreException.transition("foreign_scope_result_invalid");
                        }
                    }
                    ContentValues archived = new ContentValues();
                    archived.put("state", STATE_COMPLETED);
                    archived.putNull("result_message");
                    archived.put("result_bytes", 0);
                    archived.put("reconciliation_authorization_id", authorizationId);
                    archived.put("archive_disposition", disposition);
                    archived.put("archived_at", System.currentTimeMillis());
                    archived.put("result_sha256", resultSha256);
                    archived.put("original_result_bytes", resultBytes);
                    archived.put("updated_at", System.currentTimeMillis());
                    if (db.update(
                            TASKS,
                            archived,
                            "device_scope=? AND task_id=?",
                            new String[]{oldScope, taskId}
                    ) != 1) {
                        throw new StoreException("reconciliation_archive_update_failed");
                    }
                }
                if (OutboxReconciliationRules.authorizationComplete(
                        hasScopeNonterminal(db, oldScope)
                )) {
                    recordLocalReconciliationComplete(
                            db, authorizationId, oldScope, newScope
                    );
                }
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
            prepareForCurrentScope();
        } catch (RuntimeException failure) {
            throw storageFailure(failure);
        }
    }

    synchronized void markEmptyReconciliationComplete(
            String authorizationId,
            String oldScope,
            String newScope
    ) {
        authorizationId = requireProtocolId(authorizationId, "reconciliation_authorization_invalid");
        oldScope = TaskStoreRules.sanitizeDeviceScope(oldScope);
        newScope = TaskStoreRules.sanitizeDeviceScope(newScope);
        if (oldScope.isEmpty() || newScope.isEmpty() || oldScope.equals(newScope) ||
                !newScope.equals(CloudState.deviceId(context))) {
            throw StoreException.transition("reconciliation_scope_mismatch");
        }
        SQLiteDatabase db = requireAvailableDatabase();
        db.beginTransaction();
        try {
            if (!OutboxReconciliationRules.authorizationComplete(
                    hasScopeNonterminal(db, oldScope)
            )) {
                throw StoreException.transition("reconciliation_items_incomplete");
            }
            recordLocalReconciliationComplete(db, authorizationId, oldScope, newScope);
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
        prepareForCurrentScope();
    }

    synchronized boolean isReconciliationLocallyComplete(
            String authorizationId,
            String oldScope,
            String newScope
    ) {
        SQLiteDatabase db = requireAvailableDatabase();
        return authorizationId.equals(metaValue(db, META_RECONCILIATION_COMPLETE_ID)) &&
                oldScope.equals(metaValue(db, META_RECONCILIATION_COMPLETE_OLD_SCOPE)) &&
                newScope.equals(metaValue(db, META_RECONCILIATION_COMPLETE_NEW_SCOPE));
    }

    synchronized void acknowledgeReconciliationComplete(String authorizationId) {
        SQLiteDatabase db = requireAvailableDatabase();
        if (!authorizationId.equals(metaValue(db, META_RECONCILIATION_COMPLETE_ID))) {
            return;
        }
        db.beginTransaction();
        try {
            db.delete(META, "key IN (?,?,?)", new String[]{
                    META_RECONCILIATION_COMPLETE_ID,
                    META_RECONCILIATION_COMPLETE_OLD_SCOPE,
                    META_RECONCILIATION_COMPLETE_NEW_SCOPE,
            });
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    private static void recordLocalReconciliationComplete(
            SQLiteDatabase db,
            String authorizationId,
            String oldScope,
            String newScope
    ) {
        putMeta(db, META_RECONCILIATION_COMPLETE_ID, authorizationId);
        putMeta(db, META_RECONCILIATION_COMPLETE_OLD_SCOPE, oldScope);
        putMeta(db, META_RECONCILIATION_COMPLETE_NEW_SCOPE, newScope);
    }

    synchronized void markUnavailable(Throwable failure) {
        databaseAvailable = false;
        // Do not re-arm automatic RUNNING recovery. This process may still own a live
        // executor whose durable result has not reached SQLite yet. Cold-start recovery
        // is armed by the constructor's empty recoveredScope; service-stop recovery is
        // explicitly gated on executor termination in CloudChannelClient.stop().
        diagnosticCode = classifyStorageFailure(failure);
        publish(UNAVAILABLE, diagnosticCode, 0);
        try {
            AuditLog.append(context, "task_store_unavailable", false, new JSONObject()
                    .put("classification", diagnosticCode));
        } catch (Exception ignored) {
        }
    }

    private static void recoverInterruptedRows(SQLiteDatabase db, String scope) {
        db.beginTransaction();
        try {
            List<String> interruptedTaskIds = new ArrayList<>();
            try (Cursor cursor = db.query(
                    TASKS,
                    new String[]{"task_id"},
                    "device_scope=? AND state=?",
                    new String[]{scope, STATE_RUNNING},
                    null,
                    null,
                    null
            )) {
                while (cursor.moveToNext()) {
                    interruptedTaskIds.add(cursor.getString(0));
                }
            }
            for (String taskId : interruptedTaskIds) {
                String message = failureMessage(taskId, "task_interrupted_before_durable_result");
                ContentValues values = new ContentValues();
                values.put("state", STATE_PENDING);
                values.put("result_message", message);
                values.put("result_bytes", utf8Bytes(message));
                values.put("updated_at", System.currentTimeMillis());
                if (db.update(
                        TASKS,
                        values,
                        "device_scope=? AND task_id=?",
                        new String[]{scope, taskId}
                ) != 1) {
                    throw new StoreException("task_store_recovery_update_failed");
                }
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    private void migrateLegacyIfNeeded(SQLiteDatabase db) {
        synchronized (LEGACY_MIGRATION_LOCK) {
            migrateLegacyUnderLock(db);
        }
    }

    private void migrateLegacyUnderLock(SQLiteDatabase db) {
        String existingMigration = metaValue(db, META_MIGRATION);
        if (existingMigration != null) {
            if (META_MIGRATION_COMPLETE.equals(existingMigration)) {
                cleanupLegacyPreferencesAfterMigration(db);
            }
            return;
        }
        final Map<String, ?> values;
        try {
            values = legacyPreferences().getAll();
        } catch (RuntimeException unreadable) {
            quarantine(db, "legacy_preferences_unreadable", null, null);
            return;
        }
        if (!values.containsKey(LEGACY_LEDGER) && !values.containsKey(LEGACY_SCOPE)) {
            db.beginTransaction();
            try {
                if (metaValue(db, META_MIGRATION) == null) {
                    putMeta(db, META_MIGRATION, META_MIGRATION_COMPLETE);
                }
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
            return;
        }
        Object ledgerValue = values.get(LEGACY_LEDGER);
        Object scopeValue = values.get(LEGACY_SCOPE);
        if ((ledgerValue != null && !(ledgerValue instanceof String)) ||
                (scopeValue != null && !(scopeValue instanceof String))) {
            quarantine(db, "legacy_preferences_type_invalid", safeString(ledgerValue), safeString(scopeValue));
            return;
        }
        String encoded = ledgerValue == null ? "" : (String) ledgerValue;
        String scope = TaskStoreRules.sanitizeDeviceScope(
                scopeValue == null ? "" : (String) scopeValue
        );
        final TaskResultLedger ledger;
        try {
            ledger = TaskResultLedger.parse(encoded);
            ledger.recoverInterrupted();
            if (!ledger.records().isEmpty() && scope.isEmpty()) {
                quarantine(db, "legacy_device_scope_missing", encoded, scope);
                return;
            }
        } catch (RuntimeException invalid) {
            quarantine(db, classifyLegacyLedger(invalid), encoded, scope);
            return;
        }

        boolean migrated = false;
        db.beginTransaction();
        try {
            if (metaValue(db, META_MIGRATION) == null) {
                for (TaskResultLedger.Record record : ledger.records()) {
                    insertMigrated(db, scope, record);
                }
                putMeta(db, META_MIGRATION, META_MIGRATION_COMPLETE);
                migrated = true;
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
        boolean cleaned = false;
        if (META_MIGRATION_COMPLETE.equals(metaValue(db, META_MIGRATION))) {
            cleaned = cleanupLegacyPreferencesAfterMigration(db);
        }
        if (migrated) {
            try {
                AuditLog.append(context, "legacy_task_store_migrated", true, new JSONObject()
                        .put("record_count", ledger.records().size())
                        .put("legacy_cleanup", cleaned));
            } catch (Exception ignored) {
            }
        }
    }

    private boolean cleanupLegacyPreferencesAfterMigration(SQLiteDatabase db) {
        boolean cleaned = legacyPreferences().edit().clear().commit();
        if (!cleaned) {
            db.beginTransaction();
            try {
                putMeta(db, "legacy_cleanup_warning", "legacy_outbox_cleanup_failed");
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
        }
        return cleaned;
    }

    private void quarantine(SQLiteDatabase db, String classification, String ledger, String scope) {
        boolean diagnosticContentBounded = ledger != null &&
                ledger.length() <= MAX_TOTAL_RESULT_BYTES;
        byte[] bytes = diagnosticContentBounded
                ? ledger.getBytes(StandardCharsets.UTF_8)
                : new byte[0];
        long diagnosticBytes = diagnosticContentBounded ? bytes.length : -1L;
        String hash = diagnosticContentBounded ? sha256(bytes) : "";
        boolean quarantined = false;
        db.beginTransaction();
        try {
            if (metaValue(db, META_MIGRATION) == null) {
                ContentValues values = new ContentValues();
                values.put("classification", safeDiagnostic(classification));
                values.put("byte_size", diagnosticBytes);
                values.put("sha256", hash);
                values.put("legacy_scope", TaskStoreRules.sanitizeDeviceScope(scope));
                values.put("quarantined_at", System.currentTimeMillis());
                values.putNull("resolved_at");
                values.putNull("safe_after");
                db.insertOrThrow(QUARANTINE, null, values);
                putMeta(db, META_MIGRATION, META_MIGRATION_RECOVERY_PENDING);
                quarantined = true;
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
        if (!quarantined) {
            return;
        }
        try {
            AuditLog.append(context, "legacy_task_store_quarantined", false, new JSONObject()
                    .put("classification", safeDiagnostic(classification))
                    .put("byte_size", diagnosticBytes >= 0L ? diagnosticBytes : JSONObject.NULL)
                    .put("sha256", hash));
        } catch (Exception ignored) {
        }
    }

    private void insertMigrated(
            SQLiteDatabase db,
            String scope,
            TaskResultLedger.Record record
    ) {
        ContentValues values = new ContentValues();
        values.put("device_scope", scope);
        values.put("task_id", record.taskId);
        switch (record.state) {
            case RUNNING:
                throw new StoreException("legacy_running_not_recovered");
            case PENDING:
                values.put("state", STATE_PENDING);
                values.put("result_message", record.resultMessage);
                values.put("result_bytes", utf8Bytes(record.resultMessage));
                break;
            case COMPLETED:
                values.put("state", STATE_COMPLETED);
                values.putNull("result_message");
                values.put("result_bytes", 0);
                break;
            default:
                throw new StoreException("legacy_record_state_invalid");
        }
        values.put("updated_at", System.currentTimeMillis());
        db.insertOrThrow(TASKS, null, values);
    }

    private SQLiteDatabase requireReadyDatabase(String scope) {
        if (!publisherToken.isActive() || !databaseAvailable || !READY.equals(state) ||
                !scope.equals(readyScope)) {
            throw StoreException.transition("task_store_not_ready");
        }
        return database;
    }

    private SQLiteDatabase requireAvailableDatabase() {
        if (!databaseAvailable || database == null) {
            throw new StoreException("task_store_unavailable");
        }
        return database;
    }

    private void requireCurrentReadyScope(SQLiteDatabase db, String scope) {
        if (!scope.equals(CloudState.deviceId(context)) || !scope.equals(readyScope)) {
            throw StoreException.transition("task_store_scope_changed");
        }
        if (!OutboxReconciliationRules.channelReady(hasForeignNonterminal(db, scope))) {
            throw StoreException.transition("foreign_scope_nonterminal");
        }
    }

    private String requireScope() {
        String scope = CloudState.deviceId(context);
        if (scope.isEmpty()) {
            throw StoreException.transition("task_result_store_unbound");
        }
        return scope;
    }

    private void publishCurrentReadyStatus(SQLiteDatabase db) {
        String scope = CloudState.deviceId(context);
        if (scope.isEmpty()) {
            publish(NOT_BOUND, cleanupWarning(), 0);
        } else if (!OutboxReconciliationRules.channelReady(
                hasForeignNonterminal(db, scope)
        )) {
            publish(RECOVERY_PENDING, "foreign_scope_nonterminal", 0);
        } else {
            publishReady(scope, cleanupWarning(), countPending(db, scope));
        }
    }

    private void publishReady(String scope, String code, int pending) {
        synchronized (CloudState.class) {
            if (scope == null || scope.isEmpty() || !scope.equals(CloudState.deviceId(context))) {
                publish(UNAVAILABLE, "task_store_scope_changed", 0);
                return;
            }
            readyScope = scope;
            publish(READY, code, pending);
        }
    }

    private void publish(String nextState, String code, int pending) {
        if (!READY.equals(nextState)) {
            readyScope = "";
        }
        state = nextState;
        diagnosticCode = safeDiagnostic(code);
        QuarantineDiagnostic quarantine = databaseAvailable
                ? quarantineDiagnostic(database)
                : QuarantineDiagnostic.EMPTY;
        if (!publisherToken.isActive()) {
            return;
        }
        CloudState.setTaskStoreStatus(
                context,
                state,
                Math.max(0, pending),
                diagnosticCode,
                quarantine.byteSize,
                quarantine.sha256
        );
    }

    private String cleanupWarning() {
        if (!databaseAvailable) {
            return "";
        }
        String warning = metaValue(database, "legacy_cleanup_warning");
        return warning == null ? "" : warning;
    }

    private static StoredRecord find(SQLiteDatabase db, String scope, String taskId) {
        try (Cursor cursor = db.query(
                TASKS,
                new String[]{
                        "state",
                        "result_message",
                        "reconciliation_authorization_id",
                        "archive_disposition",
                        "result_sha256",
                        "original_result_bytes",
                },
                "device_scope=? AND task_id=?",
                new String[]{scope, taskId},
                null,
                null,
                null,
                "1"
        )) {
            return cursor.moveToFirst()
                    ? new StoredRecord(
                            cursor.getString(0),
                            cursor.isNull(1) ? null : cursor.getString(1),
                            cursor.isNull(2) ? null : cursor.getString(2),
                            cursor.isNull(3) ? null : cursor.getString(3),
                            cursor.isNull(4) ? null : cursor.getString(4),
                            cursor.getInt(5)
                    )
                    : null;
        }
    }

    private static int countInFlight(SQLiteDatabase db, String scope) {
        return scalarInt(
                db,
                "SELECT COUNT(*) FROM " + TASKS +
                        " WHERE device_scope=? AND state IN (?,?)",
                new String[]{scope, STATE_RUNNING, STATE_PENDING}
        );
    }

    private static int countInFlightAllScopes(SQLiteDatabase db) {
        return scalarInt(
                db,
                "SELECT COUNT(*) FROM " + TASKS + " WHERE state IN (?,?)",
                new String[]{STATE_RUNNING, STATE_PENDING}
        );
    }

    private static int countPending(SQLiteDatabase db, String scope) {
        return scalarInt(
                db,
                "SELECT COUNT(*) FROM " + TASKS +
                        " WHERE device_scope=? AND state=? AND archive_disposition IS NULL",
                new String[]{scope, STATE_PENDING}
        );
    }

    private static int countRows(SQLiteDatabase db) {
        return scalarInt(db, "SELECT COUNT(*) FROM " + TASKS, null);
    }

    private static int totalResultBytes(SQLiteDatabase db) {
        return scalarInt(
                db,
                "SELECT COALESCE(SUM(result_bytes),0) FROM " + TASKS,
                null
        );
    }

    private static long reservedResultBytes(SQLiteDatabase db) {
        try (Cursor cursor = db.rawQuery(
                "SELECT COALESCE(SUM(CASE WHEN state=? THEN result_bytes ELSE CAST(? AS INTEGER) END),0) " +
                        "FROM " + TASKS + " WHERE state IN (?,?)",
                new String[]{STATE_PENDING, Integer.toString(MAX_RESULT_BYTES),
                        STATE_PENDING, STATE_RUNNING}
        )) {
            if (!cursor.moveToFirst()) {
                throw new StoreException("task_store_reservation_unavailable");
            }
            return cursor.getLong(0);
        }
    }

    private static int scalarInt(SQLiteDatabase db, String sql, String[] args) {
        try (Cursor cursor = db.rawQuery(sql, args)) {
            if (!cursor.moveToFirst()) {
                throw new StoreException("task_store_count_unavailable");
            }
            return cursor.getInt(0);
        }
    }

    private static void trimCompleted(SQLiteDatabase db, String scope) {
        int completed = scalarInt(
                db,
                "SELECT COUNT(*) FROM " + TASKS +
                        " WHERE device_scope=? AND state=? AND archive_disposition IS NULL",
                new String[]{scope, STATE_COMPLETED}
        );
        int excess = completed - MAX_COMPLETED;
        if (excess > 0) {
            db.execSQL(
                    "DELETE FROM " + TASKS + " WHERE rowid IN (SELECT rowid FROM " + TASKS +
                            " WHERE device_scope=? AND state=? AND archive_disposition IS NULL " +
                            "ORDER BY updated_at ASC,rowid ASC LIMIT ?)",
                    new Object[]{scope, STATE_COMPLETED, excess}
            );
        }
        int globalCompleted = scalarInt(
                db,
                "SELECT COUNT(*) FROM " + TASKS +
                        " WHERE state=? AND archive_disposition IS NULL",
                new String[]{STATE_COMPLETED}
        );
        int globalExcess = globalCompleted - MAX_GLOBAL_COMPLETED;
        if (globalExcess > 0) {
            db.execSQL(
                    "DELETE FROM " + TASKS + " WHERE rowid IN (SELECT rowid FROM " + TASKS +
                            " WHERE state=? AND archive_disposition IS NULL " +
                            "ORDER BY updated_at ASC,rowid ASC LIMIT ?)",
                    new Object[]{STATE_COMPLETED, globalExcess}
            );
        }
    }

    private static boolean hasUnresolvedQuarantine(SQLiteDatabase db) {
        return unresolvedQuarantineCount(db) > 0;
    }

    private static boolean hasForeignNonterminal(SQLiteDatabase db, String currentScope) {
        return scalarInt(
                db,
                "SELECT COUNT(*) FROM " + TASKS +
                        " WHERE device_scope<>? AND state IN (?,?)",
                new String[]{currentScope, STATE_RUNNING, STATE_PENDING}
        ) > 0;
    }

    private static boolean hasScopeNonterminal(SQLiteDatabase db, String scope) {
        return scalarInt(
                db,
                "SELECT COUNT(*) FROM " + TASKS +
                        " WHERE device_scope=? AND state IN (?,?)",
                new String[]{scope, STATE_RUNNING, STATE_PENDING}
        ) > 0;
    }

    private static int unresolvedQuarantineCount(SQLiteDatabase db) {
        return scalarInt(
                db,
                "SELECT COUNT(*) FROM " + QUARANTINE + " WHERE resolved_at IS NULL",
                null
        );
    }

    private static boolean unresolvedQuarantineMatchesScope(SQLiteDatabase db, String scope) {
        try (Cursor cursor = db.rawQuery(
                "SELECT legacy_scope FROM " + QUARANTINE + " WHERE resolved_at IS NULL",
                null
        )) {
            boolean found = false;
            while (cursor.moveToNext()) {
                found = true;
                String legacyScope = cursor.getString(0);
                if (!TaskStoreRules.sameNonEmptyScope(scope, legacyScope)) {
                    return false;
                }
            }
            return found;
        }
    }

    private static String quarantineClassification(SQLiteDatabase db) {
        try (Cursor cursor = db.rawQuery(
                "SELECT classification FROM " + QUARANTINE +
                        " WHERE resolved_at IS NULL ORDER BY id DESC LIMIT 1",
                null
        )) {
            return cursor.moveToFirst() ? cursor.getString(0) : "legacy_recovery_pending";
        }
    }

    private static QuarantineDiagnostic quarantineDiagnostic(SQLiteDatabase db) {
        try (Cursor cursor = db.rawQuery(
                "SELECT byte_size,sha256 FROM " + QUARANTINE +
                        " WHERE resolved_at IS NULL ORDER BY id DESC LIMIT 1",
                null
        )) {
            return cursor.moveToFirst()
                    ? new QuarantineDiagnostic(cursor.getLong(0), cursor.getString(1))
                    : QuarantineDiagnostic.EMPTY;
        } catch (RuntimeException unavailable) {
            return QuarantineDiagnostic.EMPTY;
        }
    }

    private static String metaValue(SQLiteDatabase db, String key) {
        try (Cursor cursor = db.query(
                META,
                new String[]{"value"},
                "key=?",
                new String[]{key},
                null,
                null,
                null,
                "1"
        )) {
            return cursor.moveToFirst() ? cursor.getString(0) : null;
        }
    }

    private static void putMeta(SQLiteDatabase db, String key, String value) {
        ContentValues values = new ContentValues();
        values.put("key", key);
        values.put("value", value);
        if (db.insertWithOnConflict(META, null, values, SQLiteDatabase.CONFLICT_REPLACE) < 0L) {
            throw new StoreException("task_store_meta_write_failed");
        }
    }

    private static String normalizeResultMessage(String taskId, String message) {
        if (message == null || message.isEmpty() || message.indexOf('\n') >= 0 ||
                message.indexOf('\r') >= 0 || message.charAt(0) != '{' ||
                message.charAt(message.length() - 1) != '}' ||
                message.length() > MAX_RESULT_BYTES || utf8Bytes(message) > MAX_RESULT_BYTES) {
            return failureMessage(taskId, "result_too_large_for_outbox");
        }
        return message;
    }

    private static String failureMessage(String taskId, String code) {
        return "{\"type\":\"task_result\",\"protocol_version\":\"1\",\"task_id\":\"" +
                taskId + "\",\"status\":\"failed\",\"error\":{\"code\":\"" + code +
                "\",\"message\":\"Task result recovered by durable outbox contract\"}}";
    }

    private static String requireTaskId(String taskId) {
        try {
            if (taskId == null || !UUID.fromString(taskId).toString().equals(taskId)) {
                throw new IllegalArgumentException("invalid_task_id");
            }
            return taskId;
        } catch (IllegalArgumentException invalid) {
            throw invalid;
        }
    }

    private static String requireProtocolId(String value, String code) {
        try {
            if (value == null || !UUID.fromString(value).toString().equals(value)) {
                throw StoreException.transition(code);
            }
            return value;
        } catch (IllegalArgumentException invalid) {
            throw StoreException.transition(code);
        }
    }

    private static int utf8Bytes(String value) {
        return value == null ? 0 : value.getBytes(StandardCharsets.UTF_8).length;
    }

    private static String classifyLegacyLedger(RuntimeException failure) {
        String message = failure.getMessage();
        if (message != null && message.matches(
                "task_result_ledger_(too_large|corrupt|duplicate_id|limits_exceeded)"
        )) {
            return "legacy_" + message;
        }
        if (failure instanceof IllegalArgumentException) {
            return "legacy_task_id_invalid";
        }
        return "legacy_ledger_invalid";
    }

    private static String classifyStorageFailure(Throwable failure) {
        Throwable current = failure;
        while (current != null) {
            if (current instanceof SQLiteFullException) {
                return "task_store_sqlite_full";
            }
            if (current instanceof SQLiteDatabaseCorruptException) {
                return "task_store_sqlite_corrupt";
            }
            if (current instanceof SQLiteDiskIOException) {
                return "task_store_sqlite_io";
            }
            if (current instanceof SQLiteException) {
                return "task_store_sqlite_error";
            }
            if (current instanceof StoreException) {
                return ((StoreException) current).code;
            }
            current = current.getCause();
        }
        return "task_store_unavailable";
    }

    private static StoreException storageFailure(RuntimeException failure) {
        return failure instanceof StoreException
                ? (StoreException) failure
                : new StoreException(classifyStorageFailure(failure), failure);
    }

    static boolean isStorageUnavailableFailure(Throwable failure) {
        Throwable current = failure;
        while (current != null) {
            if (current instanceof StoreException) {
                return ((StoreException) current).storageUnavailable;
            }
            if (current instanceof SQLiteException) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    private static String safeDiagnostic(String value) {
        if (value == null || !value.matches("[A-Za-z0-9_.-]{0,80}")) {
            return value == null || value.isEmpty() ? "" : "task_store_error";
        }
        return value;
    }

    private static String safeString(Object value) {
        return value instanceof String ? (String) value : null;
    }

    private static String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder hex = new StringBuilder(digest.length * 2);
            for (byte value : digest) {
                hex.append(String.format(java.util.Locale.ROOT, "%02x", value & 0xff));
            }
            return hex.toString();
        } catch (Exception unavailable) {
            return "sha256_unavailable";
        }
    }

    private static final class StoredRecord {
        final String state;
        final String resultMessage;
        final String reconciliationAuthorizationId;
        final String archiveDisposition;
        final String resultSha256;
        final int originalResultBytes;

        StoredRecord(
                String state,
                String resultMessage,
                String reconciliationAuthorizationId,
                String archiveDisposition,
                String resultSha256,
                int originalResultBytes
        ) {
            this.state = state;
            this.resultMessage = resultMessage;
            this.reconciliationAuthorizationId = reconciliationAuthorizationId;
            this.archiveDisposition = archiveDisposition;
            this.resultSha256 = resultSha256 == null ? "" : resultSha256;
            this.originalResultBytes = originalResultBytes;
        }
    }

    private static final class QuarantineDiagnostic {
        static final QuarantineDiagnostic EMPTY = new QuarantineDiagnostic(-1L, "");
        final long byteSize;
        final String sha256;

        QuarantineDiagnostic(long byteSize, String sha256) {
            this.byteSize = byteSize;
            this.sha256 = sha256 == null ? "" : sha256;
        }
    }

    private static final class DatabaseHelper extends SQLiteOpenHelper {
        private static final DatabaseErrorHandler PRESERVE_CORRUPT_DATABASE = db -> {
            try {
                db.close();
            } catch (RuntimeException ignored) {
            }
            throw new SQLiteDatabaseCorruptException("task_store_corrupt_preserved");
        };

        DatabaseHelper(Context context) {
            super(
                    context,
                    DATABASE_NAME,
                    null,
                    DATABASE_VERSION,
                    PRESERVE_CORRUPT_DATABASE
            );
        }

        @Override
        public void onConfigure(SQLiteDatabase db) {
            super.onConfigure(db);
            db.setForeignKeyConstraintsEnabled(true);
        }

        @Override
        public void onCreate(SQLiteDatabase db) {
            db.execSQL(
                    "CREATE TABLE " + TASKS + " (" +
                            "device_scope TEXT NOT NULL," +
                            "task_id TEXT NOT NULL," +
                            "state TEXT NOT NULL CHECK(state IN ('RUNNING','PENDING','COMPLETED'))," +
                            "result_message TEXT," +
                            "result_bytes INTEGER NOT NULL DEFAULT 0 CHECK(result_bytes>=0 AND result_bytes<=" +
                            MAX_RESULT_BYTES + ")," +
                            "reconciliation_authorization_id TEXT," +
                            "archive_disposition TEXT CHECK(archive_disposition IS NULL OR " +
                            "archive_disposition IN ('expired_with_late_result'," +
                            "'admin_revoked_with_late_result','admin_revoked_interrupted'))," +
                            "archived_at INTEGER," +
                            "result_sha256 TEXT," +
                            "original_result_bytes INTEGER NOT NULL DEFAULT 0 " +
                            "CHECK(original_result_bytes>=0 AND original_result_bytes<=" +
                            MAX_RESULT_BYTES + ")," +
                            "updated_at INTEGER NOT NULL," +
                            "PRIMARY KEY(device_scope,task_id)," +
                            "CHECK((state='PENDING' AND result_message IS NOT NULL AND result_bytes>0) OR " +
                            "(state!='PENDING' AND result_message IS NULL AND result_bytes=0))," +
                            "CHECK((archive_disposition IS NULL AND reconciliation_authorization_id IS NULL " +
                            "AND archived_at IS NULL AND result_sha256 IS NULL AND original_result_bytes=0) OR " +
                            "(state='COMPLETED' AND archive_disposition IS NOT NULL " +
                            "AND reconciliation_authorization_id IS NOT NULL AND archived_at IS NOT NULL " +
                            "AND result_sha256 IS NOT NULL))" +
                            ")"
            );
            db.execSQL(
                    "CREATE INDEX idx_task_results_state_time ON " + TASKS +
                            "(state,updated_at)"
            );
            db.execSQL(
                    "CREATE INDEX idx_task_results_scope_archive ON " + TASKS +
                            "(device_scope,archive_disposition,updated_at)"
            );
            db.execSQL(
                    "CREATE TABLE " + META + " (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
            );
            db.execSQL(
                    "CREATE TABLE " + QUARANTINE + " (" +
                            "id INTEGER PRIMARY KEY AUTOINCREMENT," +
                            "classification TEXT NOT NULL," +
                            "byte_size INTEGER NOT NULL," +
                            "sha256 TEXT NOT NULL," +
                            "legacy_scope TEXT NOT NULL," +
                            "quarantined_at INTEGER NOT NULL," +
                            "resolved_at INTEGER," +
                            "safe_after INTEGER" +
                            ")"
            );
        }

        @Override
        public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
            if (oldVersion == 1 && newVersion == 2) {
                db.beginTransaction();
                try {
                    db.execSQL("ALTER TABLE " + TASKS +
                            " ADD COLUMN reconciliation_authorization_id TEXT");
                    db.execSQL("ALTER TABLE " + TASKS +
                            " ADD COLUMN archive_disposition TEXT");
                    db.execSQL("ALTER TABLE " + TASKS + " ADD COLUMN archived_at INTEGER");
                    db.execSQL("ALTER TABLE " + TASKS + " ADD COLUMN result_sha256 TEXT");
                    db.execSQL("ALTER TABLE " + TASKS +
                            " ADD COLUMN original_result_bytes INTEGER NOT NULL DEFAULT 0");
                    db.execSQL("CREATE INDEX idx_task_results_scope_archive ON " + TASKS +
                            "(device_scope,archive_disposition,updated_at)");
                    db.setTransactionSuccessful();
                } finally {
                    db.endTransaction();
                }
                return;
            }
            throw new SQLiteException("task_store_schema_upgrade_required");
        }

        @Override
        public void onDowngrade(SQLiteDatabase db, int oldVersion, int newVersion) {
            throw new SQLiteException("task_store_schema_downgrade_rejected");
        }
    }
}
