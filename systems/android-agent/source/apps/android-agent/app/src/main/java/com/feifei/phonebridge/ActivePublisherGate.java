package com.feifei.phonebridge;

/** Process-local ownership gate so a retired store cannot overwrite current UI/capability state. */
final class ActivePublisherGate {
    private long nextGeneration;
    private long activeGeneration;

    synchronized Token activate() {
        activeGeneration = ++nextGeneration;
        return new Token(this, activeGeneration);
    }

    private synchronized boolean isActive(long generation) {
        return generation == activeGeneration;
    }

    static final class Token {
        private final ActivePublisherGate owner;
        private final long generation;

        private Token(ActivePublisherGate owner, long generation) {
            this.owner = owner;
            this.generation = generation;
        }

        boolean isActive() {
            return owner.isActive(generation);
        }
    }
}
