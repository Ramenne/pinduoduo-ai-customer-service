package com.feifei.phonebridge;

/**
 * Authoritative connection lifecycle and reconnect-deadline state.
 *
 * <p>This class is deliberately Android-free so timing, cancellation ordering, and recovery can
 * be tested deterministically. Socket ownership remains in {@link CloudChannelClient}; every
 * socket retirement must prepare a reconnect deadline here before the transport is closed or
 * cancelled.</p>
 */
final class CloudConnectionLifecycle {
    static final String BOUND_OFFLINE = "BOUND_OFFLINE";
    static final String CONNECTING = "CONNECTING";
    static final String ONLINE_FRESH = "ONLINE_FRESH";
    static final String SUSPECT = "SUSPECT";
    static final String RECONNECT_WAIT = "RECONNECT_WAIT";
    static final long NO_DEADLINE = BoundedReconnectBackoff.NO_DEADLINE;

    static final class Snapshot {
        final String state;
        final String reason;
        final long retryAtElapsedMillis;
        final int reconnectAttempt;

        Snapshot(
                String state,
                String reason,
                long retryAtElapsedMillis,
                int reconnectAttempt
        ) {
            this.state = state;
            this.reason = reason;
            this.retryAtElapsedMillis = retryAtElapsedMillis;
            this.reconnectAttempt = reconnectAttempt;
        }
    }

    private final BoundedReconnectBackoff backoff;
    private String state = BOUND_OFFLINE;
    private String reason = "not_started";
    private int reconnectAttempt;

    CloudConnectionLifecycle(long maximumReconnectDelayMillis) {
        backoff = new BoundedReconnectBackoff(maximumReconnectDelayMillis);
    }

    synchronized Snapshot markBoundOffline(String transitionReason) {
        backoff.clear();
        reconnectAttempt = 0;
        state = BOUND_OFFLINE;
        reason = safeReason(transitionReason);
        return snapshot();
    }

    synchronized Snapshot markOnlineFresh() {
        backoff.authenticated();
        // This counter describes the current reconnect cycle, not process lifetime.
        reconnectAttempt = 0;
        state = ONLINE_FRESH;
        reason = "server_activity_fresh";
        return snapshot();
    }

    synchronized Snapshot markSuspect(String transitionReason) {
        if (ONLINE_FRESH.equals(state) || SUSPECT.equals(state)) {
            state = SUSPECT;
            reason = safeReason(transitionReason);
        }
        return snapshot();
    }

    /** Registers an immediate replacement before the current socket is retired. */
    synchronized Snapshot prepareImmediateReconnect(long nowElapsedMillis, String transitionReason) {
        backoff.retryNow(nowElapsedMillis);
        reason = safeReason(transitionReason);
        return snapshot();
    }

    /** Registers bounded backoff unless a caller already prepared an earlier replacement. */
    synchronized Snapshot prepareBackoffReconnect(
            long nowElapsedMillis,
            long jitterMillis,
            String transitionReason
    ) {
        if (backoff.retryAtElapsedMillis() == NO_DEADLINE) {
            backoff.schedule(nowElapsedMillis, jitterMillis);
        }
        reason = safeReason(transitionReason);
        return snapshot();
    }

    synchronized Snapshot enterReconnectWait(String transitionReason) {
        if (backoff.retryAtElapsedMillis() == NO_DEADLINE) {
            throw new IllegalStateException("reconnect_deadline_not_registered");
        }
        state = RECONNECT_WAIT;
        reason = safeReason(transitionReason);
        return snapshot();
    }

    synchronized boolean claimReconnectIfDue(long nowElapsedMillis) {
        if (!backoff.claimIfDue(nowElapsedMillis)) {
            return false;
        }
        reconnectAttempt++;
        state = CONNECTING;
        reason = "connect_attempt";
        return true;
    }

    synchronized Snapshot markConnecting() {
        state = CONNECTING;
        reason = "connect_attempt";
        return snapshot();
    }

    synchronized long retryAtElapsedMillis() {
        return backoff.retryAtElapsedMillis();
    }

    synchronized boolean hasReconnectDeadline() {
        return backoff.retryAtElapsedMillis() != NO_DEADLINE;
    }

    synchronized Snapshot snapshot() {
        return new Snapshot(
                state,
                reason,
                backoff.retryAtElapsedMillis(),
                reconnectAttempt
        );
    }

    private static String safeReason(String value) {
        if (value == null || value.trim().isEmpty()) {
            return "unspecified";
        }
        String trimmed = value.trim();
        return trimmed.length() <= 80 ? trimmed : trimmed.substring(0, 80);
    }
}
