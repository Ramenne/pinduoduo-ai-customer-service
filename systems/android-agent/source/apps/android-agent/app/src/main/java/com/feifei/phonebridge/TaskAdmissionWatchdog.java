package com.feifei.phonebridge;

/** Actor-visible generation fence for a potentially blocked durable task claim. */
final class TaskAdmissionWatchdog {
    private long generation;
    private boolean inFlight;
    private boolean timedOut;

    long begin() {
        if (inFlight) {
            return -1L;
        }
        inFlight = true;
        timedOut = false;
        return ++generation;
    }

    boolean complete(long completedGeneration) {
        if (!inFlight || completedGeneration != generation) {
            return false;
        }
        inFlight = false;
        timedOut = false;
        return true;
    }

    boolean timeout(long timedOutGeneration) {
        if (!inFlight || timedOutGeneration != generation) {
            return false;
        }
        timedOut = true;
        return true;
    }

    boolean inFlight() {
        return inFlight;
    }

    boolean timedOut() {
        return timedOut;
    }
}
