package com.feifei.phonebridge;

/** Pure contract for closing durable claim ownership when its delivery lease expires. */
final class ExpiredTaskAdmissionRules {
    static final String DURABLE_FAILURE = "DURABLE_FAILURE";
    static final String PRESERVE_EXISTING_TERMINAL = "PRESERVE_EXISTING_TERMINAL";

    private ExpiredTaskAdmissionRules() {
    }

    static String disposition(TaskResultLedger.ClaimKind claimKind) {
        return claimKind == TaskResultLedger.ClaimKind.DEADLINE_EXPIRED
                ? DURABLE_FAILURE
                : PRESERVE_EXISTING_TERMINAL;
    }

    static boolean mustPersistFailure(TaskResultLedger.ClaimKind claimKind) {
        return DURABLE_FAILURE.equals(disposition(claimKind));
    }
}
