package com.feifei.phonebridge;

final class ReconnectPreflight {
    static final String READY = "reconnect_ready";
    static final String SERVICE_NOT_RUNNING = "service_not_running";
    static final String DEVICE_REVOKED = "device_revoked";
    static final String CONFIG_REQUIRED = "cloud_base_url_not_configured";
    static final String DEVICE_UNBOUND = "device_unbound";
    static final String NETWORK_UNAVAILABLE = "network_unavailable";

    private ReconnectPreflight() {
    }

    static String evaluate(
            boolean serviceRunning,
            boolean revoked,
            boolean configured,
            boolean bound,
            boolean networkAvailable
    ) {
        if (!serviceRunning) {
            return SERVICE_NOT_RUNNING;
        }
        if (revoked) {
            return DEVICE_REVOKED;
        }
        if (!configured) {
            return CONFIG_REQUIRED;
        }
        if (!bound) {
            return DEVICE_UNBOUND;
        }
        if (!networkAvailable) {
            return NETWORK_UNAVAILABLE;
        }
        return READY;
    }
}
