package com.feifei.phonebridge;

/** Android-free recovery-budget state owned exclusively by the transport actor. */
final class RecoveryEpisode {
    static final String NEVER = "never";
    static final String IN_PROGRESS = "in_progress";
    static final String RECOVERED = "recovered";
    static final String BUDGET_EXHAUSTED = "budget_exhausted";
    static final String BLOCKED = "blocked";

    static final class Snapshot {
        final long generation;
        final long startedElapsedMillis;
        final long completedElapsedMillis;
        final long deadlineElapsedMillis;
        final int attempts;
        final String trigger;
        final String failureStage;
        final String outcome;

        Snapshot(long generation, long startedElapsedMillis, long completedElapsedMillis,
                long deadlineElapsedMillis, int attempts, String trigger,
                String failureStage, String outcome) {
            this.generation = generation;
            this.startedElapsedMillis = startedElapsedMillis;
            this.completedElapsedMillis = completedElapsedMillis;
            this.deadlineElapsedMillis = deadlineElapsedMillis;
            this.attempts = attempts;
            this.trigger = trigger;
            this.failureStage = failureStage;
            this.outcome = outcome;
        }
    }

    private final long maximumBudgetMillis;
    private long generation;
    private long started = -1L;
    private long completed = -1L;
    private long deadline = -1L;
    private int attempts;
    private String trigger = NEVER;
    private String failureStage = "none";
    private String outcome = NEVER;
    private boolean acceptsNewEpisode = true;

    RecoveryEpisode(long maximumBudgetMillis) {
        if (maximumBudgetMillis <= 0L) {
            throw new IllegalArgumentException("invalid_recovery_budget");
        }
        this.maximumBudgetMillis = maximumBudgetMillis;
    }

    Snapshot startIfIdle(long nowElapsedMillis, String episodeTrigger) {
        return startIfIdle(nowElapsedMillis, episodeTrigger, maximumBudgetMillis);
    }

    Snapshot startIfIdle(long startElapsedMillis, String episodeTrigger, long budgetMillis) {
        if (IN_PROGRESS.equals(outcome) || !acceptsNewEpisode) {
            return snapshot();
        }
        if (startElapsedMillis < 0L || budgetMillis <= 0L ||
                budgetMillis > maximumBudgetMillis) {
            throw new IllegalArgumentException("invalid_recovery_episode_start");
        }
        generation++;
        started = startElapsedMillis;
        completed = -1L;
        deadline = startElapsedMillis + budgetMillis;
        attempts = 0;
        trigger = safe(episodeTrigger, NEVER);
        failureStage = "none";
        outcome = IN_PROGRESS;
        acceptsNewEpisode = false;
        return snapshot();
    }

    Snapshot recordAttempt() {
        if (IN_PROGRESS.equals(outcome)) {
            attempts++;
        }
        return snapshot();
    }

    Snapshot recordFailure(String stage) {
        if (IN_PROGRESS.equals(outcome)) {
            failureStage = safe(stage, "unknown");
        }
        return snapshot();
    }

    Snapshot recovered(long nowElapsedMillis) {
        if (IN_PROGRESS.equals(outcome)) {
            completed = nowElapsedMillis;
            if (nowElapsedMillis >= deadline) {
                failureStage = "recovery_budget";
                outcome = BUDGET_EXHAUSTED;
            } else {
                outcome = RECOVERED;
            }
        }
        return snapshot();
    }

    Snapshot blocked(long nowElapsedMillis, String stage) {
        if (IN_PROGRESS.equals(outcome)) {
            completed = nowElapsedMillis;
            failureStage = safe(stage, "unknown");
            outcome = BLOCKED;
        }
        return snapshot();
    }

    Snapshot expireIfDue(long nowElapsedMillis) {
        if (IN_PROGRESS.equals(outcome) && nowElapsedMillis >= deadline) {
            completed = nowElapsedMillis;
            failureStage = "recovery_budget";
            outcome = BUDGET_EXHAUSTED;
        }
        return snapshot();
    }

    boolean isInProgress() {
        return IN_PROGRESS.equals(outcome);
    }

    void allowNextEpisode() {
        if (!IN_PROGRESS.equals(outcome)) {
            acceptsNewEpisode = true;
        }
    }

    boolean isDue(long nowElapsedMillis) {
        return isInProgress() && nowElapsedMillis >= deadline;
    }

    long deadlineElapsedMillis() {
        return deadline;
    }

    Snapshot snapshot() {
        return new Snapshot(generation, started, completed, deadline, attempts,
                trigger, failureStage, outcome);
    }

    private static String safe(String value, String fallback) {
        if (value == null || value.trim().isEmpty()) {
            return fallback;
        }
        String trimmed = value.trim();
        return trimmed.length() <= 80 ? trimmed : trimmed.substring(0, 80);
    }
}
