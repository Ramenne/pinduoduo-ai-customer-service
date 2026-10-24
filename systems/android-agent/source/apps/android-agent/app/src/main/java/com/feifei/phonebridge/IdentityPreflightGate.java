package com.feifei.phonebridge;

/** Actor-owned generation/epoch gate for asynchronous identity recovery. */
final class IdentityPreflightGate {
    static final class Request {
        final long generation;
        final long epoch;

        Request(long generation, long epoch) {
            this.generation = generation;
            this.epoch = epoch;
        }
    }

    static final String CURRENT = "CURRENT";
    static final String STALE_EPOCH_RETRY = "STALE_EPOCH_RETRY";
    static final String OBSOLETE_GENERATION = "OBSOLETE_GENERATION";

    private long generation;
    private boolean inFlight;

    Request begin(long epoch) {
        if (inFlight) {
            return null;
        }
        inFlight = true;
        return new Request(++generation, epoch);
    }

    String complete(long completedGeneration, long requestedEpoch, long currentEpoch) {
        if (completedGeneration != generation) {
            return OBSOLETE_GENERATION;
        }
        inFlight = false;
        return requestedEpoch == currentEpoch ? CURRENT : STALE_EPOCH_RETRY;
    }

    boolean timeout(long timedOutGeneration) {
        if (timedOutGeneration != generation || !inFlight) {
            return false;
        }
        inFlight = false;
        generation++;
        return true;
    }

    boolean isInFlight() {
        return inFlight;
    }
}
