package com.feifei.phonebridge;

/** Pure policy helpers shared by the SQLite store and narrow JVM tests. */
final class TaskStoreRules {
    private TaskStoreRules() {
    }

    static boolean canClaim(
            int currentScopeInFlight,
            int globalInFlight,
            int totalRows,
            long reservedResultBytes,
            int maxInFlight,
            int maxRows,
            long maxTotalResultBytes,
            int perResultReservationBytes
    ) {
        return currentScopeInFlight >= 0 && currentScopeInFlight < maxInFlight &&
                globalInFlight >= 0 && globalInFlight < maxInFlight &&
                totalRows >= 0 && totalRows < maxRows &&
                reservedResultBytes >= 0L &&
                reservedResultBytes <= maxTotalResultBytes - perResultReservationBytes;
    }

    static boolean sameNonEmptyScope(String currentScope, String legacyScope) {
        return currentScope != null && !currentScope.isEmpty() && currentScope.equals(legacyScope);
    }

    static String sanitizeDeviceScope(String supplied) {
        if (supplied == null || supplied.length() == 0 || supplied.length() > 80 ||
                !supplied.matches("[A-Za-z0-9._:-]{1,80}")) {
            return "";
        }
        return supplied;
    }

    static boolean recoveryTimeReached(long safeAfter, long trustedServerNow) {
        return safeAfter > 0L && trustedServerNow >= safeAfter;
    }

    static long normalizeDiagnosticByteSize(long supplied) {
        return supplied < 0L ? -1L : supplied;
    }

    static boolean canRecoverAfterExecutorStop(boolean executorTerminated, boolean storeReady) {
        return executorTerminated && storeReady;
    }

    static boolean shouldRecoverOnPrepare(String recoveredScope, String currentScope) {
        return currentScope != null && !currentScope.isEmpty() &&
                !currentScope.equals(recoveredScope);
    }

    static boolean canAdmitTask(boolean running, boolean currentAuthenticated, boolean executorAccepting) {
        return running && currentAuthenticated && executorAccepting;
    }
}
