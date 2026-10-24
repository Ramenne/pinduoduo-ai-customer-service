package com.feifei.phonebridge;

/**
 * Tracks authenticated server activity with a monotonic clock.
 *
 * <p>The active session is part of the state so a late callback from a replaced socket cannot
 * make the current channel look healthy. The caller supplies elapsed-realtime values; wall clock
 * is only accepted by the display helper and is never used for reconnect decisions.</p>
 */
final class ServerActivityWatchdog<T> {
    private static final long NO_ACTIVITY = Long.MIN_VALUE;

    enum Health {
        INACTIVE,
        FRESH,
        SUSPECT,
        RETIRE_DUE
    }

    static final class Observation<T> {
        final T session;
        final long activityGeneration;
        final Health health;
        final long retireDeadlineElapsedMillis;

        Observation(
                T session,
                long activityGeneration,
                Health health,
                long retireDeadlineElapsedMillis
        ) {
            this.session = session;
            this.activityGeneration = activityGeneration;
            this.health = health;
            this.retireDeadlineElapsedMillis = retireDeadlineElapsedMillis;
        }
    }

    private final long suspectAfterMillis;
    private final long retireAfterMillis;
    private T activeSession;
    private long lastActivityElapsedMillis = NO_ACTIVITY;
    private long activityGeneration;

    ServerActivityWatchdog(long timeoutMillis) {
        this(timeoutMillis, timeoutMillis);
    }

    ServerActivityWatchdog(long suspectAfterMillis, long retireAfterMillis) {
        if (suspectAfterMillis <= 0L || retireAfterMillis < suspectAfterMillis) {
            throw new IllegalArgumentException("invalid_activity_deadlines");
        }
        this.suspectAfterMillis = suspectAfterMillis;
        this.retireAfterMillis = retireAfterMillis;
    }

    synchronized void begin(T session, long elapsedRealtimeMillis) {
        if (session == null || elapsedRealtimeMillis < 0L) {
            throw new IllegalArgumentException("invalid_server_activity");
        }
        activeSession = session;
        lastActivityElapsedMillis = elapsedRealtimeMillis;
        activityGeneration++;
    }

    synchronized boolean acknowledge(T session, long elapsedRealtimeMillis) {
        if (activeSession != session || elapsedRealtimeMillis < lastActivityElapsedMillis) {
            return false;
        }
        lastActivityElapsedMillis = elapsedRealtimeMillis;
        activityGeneration++;
        return true;
    }

    synchronized Observation<T> observe(T session, long elapsedRealtimeMillis) {
        return new Observation<>(
                session,
                activityGeneration,
                health(session, elapsedRealtimeMillis),
                retireDeadlineElapsedMillis(session)
        );
    }

    synchronized boolean isCurrent(Observation<T> observation) {
        return observation != null && activeSession == observation.session &&
                activityGeneration == observation.activityGeneration;
    }

    synchronized boolean isStale(T session, long elapsedRealtimeMillis) {
        Health health = health(session, elapsedRealtimeMillis);
        return health == Health.SUSPECT || health == Health.RETIRE_DUE;
    }

    synchronized boolean isRetireDue(T session, long elapsedRealtimeMillis) {
        return health(session, elapsedRealtimeMillis) == Health.RETIRE_DUE;
    }

    synchronized Health health(T session, long elapsedRealtimeMillis) {
        if (activeSession != session || lastActivityElapsedMillis == NO_ACTIVITY ||
                elapsedRealtimeMillis < lastActivityElapsedMillis) {
            return Health.INACTIVE;
        }
        long ageMillis = elapsedRealtimeMillis - lastActivityElapsedMillis;
        if (ageMillis >= retireAfterMillis) {
            return Health.RETIRE_DUE;
        }
        if (ageMillis >= suspectAfterMillis) {
            return Health.SUSPECT;
        }
        return Health.FRESH;
    }

    synchronized long retireDeadlineElapsedMillis(T session) {
        if (activeSession != session || lastActivityElapsedMillis == NO_ACTIVITY) {
            return -1L;
        }
        return lastActivityElapsedMillis + retireAfterMillis;
    }

    synchronized void clear(T session) {
        if (activeSession == session) {
            clearAll();
        }
    }

    synchronized void clearAll() {
        activeSession = null;
        lastActivityElapsedMillis = NO_ACTIVITY;
        activityGeneration++;
    }

    static boolean isFreshWallClock(long activityAtMillis, long nowMillis, long timeoutMillis) {
        return activityAtMillis > 0L &&
                timeoutMillis > 0L &&
                nowMillis >= activityAtMillis &&
                nowMillis - activityAtMillis < timeoutMillis;
    }

    static long displayAgeMillis(long activityAtMillis, long nowMillis) {
        if (activityAtMillis <= 0L || nowMillis < activityAtMillis) {
            return -1L;
        }
        return nowMillis - activityAtMillis;
    }
}
