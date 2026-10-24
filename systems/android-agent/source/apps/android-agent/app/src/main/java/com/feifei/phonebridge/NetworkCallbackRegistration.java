package com.feifei.phonebridge;

/** Actor-owned truth for asynchronous ConnectivityManager callback registration. */
final class NetworkCallbackRegistration {
    static final String UNREGISTERED = "UNREGISTERED";
    static final String REGISTERING = "REGISTERING";
    static final String REGISTERED = "REGISTERED";

    private String state = UNREGISTERED;
    private long generation;

    long begin() {
        if (!UNREGISTERED.equals(state)) {
            return -1L;
        }
        state = REGISTERING;
        return ++generation;
    }

    boolean complete(long completedGeneration, boolean success) {
        if (completedGeneration != generation || !REGISTERING.equals(state)) {
            return false;
        }
        state = success ? REGISTERED : UNREGISTERED;
        return true;
    }

    boolean timeout(long timedOutGeneration) {
        if (timedOutGeneration != generation || !REGISTERING.equals(state)) {
            return false;
        }
        generation++;
        state = UNREGISTERED;
        return true;
    }

    void stopped() {
        generation++;
        state = UNREGISTERED;
    }

    boolean isRegistered() {
        return REGISTERED.equals(state);
    }

    String state() {
        return state;
    }
}
