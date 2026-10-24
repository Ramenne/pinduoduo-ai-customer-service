package com.feifei.phonebridge;

/** Immutable five-field cross-process recovery lease. */
final class RecoveryLease {
    static final String OWNER_MAIN = "main";
    static final String OWNER_STOPPED = "stopped";
    static final String OWNER_NETWORK_BLOCKED = "blocked:network";
    static final String OWNER_TERMINAL_BLOCKED = "blocked:terminal";
    static final String OWNER_GUARDIAN_START_REJECTED =
            "blocked:guardian_start_rejected";
    static final String OWNER_GUARDIAN_HANDOFF_TIMEOUT =
            "blocked:guardian_handoff_timeout";
    static final String OWNER_GUARDIAN_INTERNAL_FAILURE =
            "blocked:guardian_internal_failure";
    static final String OWNER_MAIN_RECOVERY_PREFIX = "main:recovery:";
    static final String OWNER_GUARDIAN_PENDING_PREFIX = "guardian:pending:";

    final long generation;
    final String processInstanceId;
    final long lastProgressElapsed;
    final long deadlineElapsed;
    final String recoveryOwner;

    RecoveryLease(
            long generation,
            String processInstanceId,
            long lastProgressElapsed,
            long deadlineElapsed,
            String recoveryOwner
    ) {
        this.generation = generation;
        this.processInstanceId = safe(processInstanceId);
        this.lastProgressElapsed = lastProgressElapsed;
        this.deadlineElapsed = deadlineElapsed;
        this.recoveryOwner = safe(recoveryOwner);
    }

    boolean belongsTo(long expectedGeneration, String expectedProcessInstanceId) {
        return generation == expectedGeneration &&
                processInstanceId.equals(safe(expectedProcessInstanceId));
    }

    boolean isOwnedByMain() {
        return OWNER_MAIN.equals(recoveryOwner);
    }

    boolean isGuardianClaimable() {
        return isOwnedByMain() || recoveryOwner.startsWith(OWNER_MAIN_RECOVERY_PREFIX);
    }

    boolean isGuardianPending() {
        return recoveryOwner.startsWith(OWNER_GUARDIAN_PENDING_PREFIX);
    }

    boolean isGuardianFailureBlocked() {
        return OWNER_GUARDIAN_START_REJECTED.equals(recoveryOwner) ||
                OWNER_GUARDIAN_HANDOFF_TIMEOUT.equals(recoveryOwner) ||
                OWNER_GUARDIAN_INTERNAL_FAILURE.equals(recoveryOwner);
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
