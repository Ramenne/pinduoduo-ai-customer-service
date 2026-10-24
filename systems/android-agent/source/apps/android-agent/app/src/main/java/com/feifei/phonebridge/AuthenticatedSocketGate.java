package com.feifei.phonebridge;

/**
 * Tracks which transport has completed device authentication. Callers provide the current
 * transport while holding their connection lock, so a persisted result is never routed to an
 * unauthenticated or superseded connection.
 */
final class AuthenticatedSocketGate<T> {
    private T authenticated;

    void markAuthenticated(T socket) {
        if (socket == null) {
            throw new IllegalArgumentException("authenticated_socket_required");
        }
        authenticated = socket;
    }

    T select(T current) {
        return current != null && authenticated == current ? current : null;
    }

    boolean isAuthenticated(T current) {
        return current != null && authenticated == current;
    }

    boolean deliveryConfirmed(T target, boolean sendAccepted, T current) {
        return sendAccepted && target != null && authenticated == target && current == target;
    }

    void clear(T socket) {
        if (authenticated == socket) {
            authenticated = null;
        }
    }

    void clearAll() {
        authenticated = null;
    }
}
