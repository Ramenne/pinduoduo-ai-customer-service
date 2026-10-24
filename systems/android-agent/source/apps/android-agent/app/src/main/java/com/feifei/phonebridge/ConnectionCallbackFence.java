package com.feifei.phonebridge;

final class ConnectionCallbackFence {
    private ConnectionCallbackFence() {
    }

    static <T> boolean mayPublishAuthenticatedMessage(
            long listenerEpoch,
            long currentEpoch,
            T callbackSocket,
            T currentSocket,
            boolean authenticated
    ) {
        return listenerEpoch == currentEpoch && callbackSocket != null &&
                callbackSocket == currentSocket && authenticated;
    }
}
