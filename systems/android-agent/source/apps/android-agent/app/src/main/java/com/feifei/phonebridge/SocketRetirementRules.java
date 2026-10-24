package com.feifei.phonebridge;

final class SocketRetirementRules {
    private SocketRetirementRules() {
    }

    static boolean mayRetire(
            String reason,
            boolean authenticated,
            boolean heartbeatRetireDue,
            long installedAtElapsedMillis,
            long nowElapsedMillis,
            long authenticationTimeoutMillis
    ) {
        if (reason == null || reason.isEmpty()) {
            return false;
        }
        if ("heartbeat_ack_grace_exhausted".equals(reason)) {
            return authenticated && heartbeatRetireDue;
        }
        if ("cloud_auth_timeout".equals(reason)) {
            return !authenticated && installedAtElapsedMillis >= 0L &&
                    nowElapsedMillis >= installedAtElapsedMillis &&
                    nowElapsedMillis - installedAtElapsedMillis >= authenticationTimeoutMillis;
        }
        return true;
    }
}
