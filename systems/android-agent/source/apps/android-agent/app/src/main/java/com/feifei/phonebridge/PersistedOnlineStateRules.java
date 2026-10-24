package com.feifei.phonebridge;

/** Fail-closed projection for process-persistent transport state. */
final class PersistedOnlineStateRules {
    private PersistedOnlineStateRules() {
    }

    static String effectiveState(
            String stored,
            boolean bound,
            boolean serverActivityFresh
    ) {
        if ("ONLINE_FRESH".equals(stored) && !serverActivityFresh) {
            return bound ? "SUSPECT" : "UNBOUND";
        }
        return stored;
    }
}
