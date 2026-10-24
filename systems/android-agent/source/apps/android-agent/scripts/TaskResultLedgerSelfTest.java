package com.feifei.phonebridge;

public final class TaskResultLedgerSelfTest {
    private static final String FIRST = "11111111-1111-4111-8111-111111111111";
    private static final String SECOND = "22222222-2222-4222-8222-222222222222";
    private static final String THIRD = "33333333-3333-4333-8333-333333333333";

    public static void main(String[] args) {
        TaskResultLedger ledger = TaskResultLedger.empty();
        require(ledger.claim(FIRST).kind == TaskResultLedger.ClaimKind.NEW, "first claim");
        String result = "{\"type\":\"task_result\",\"protocol_version\":\"1\",\"task_id\":\"" +
                FIRST + "\",\"status\":\"succeeded\",\"result\":{\"ok\":true}}";
        require(result.equals(ledger.complete(FIRST, result)), "durable result");

        TaskResultLedger restarted = TaskResultLedger.parse(ledger.encode());
        TaskResultLedger.Claim replay = restarted.claim(FIRST);
        require(replay.kind == TaskResultLedger.ClaimKind.PENDING, "pending after restart");
        require(result.equals(replay.pendingMessage), "same result after restart");
        require(restarted.pendingMessages().size() == 1, "one pending result");
        require(restarted.acknowledge(FIRST), "acknowledge");
        require(restarted.claim(FIRST).kind == TaskResultLedger.ClaimKind.COMPLETED, "no re-execution after ack");

        require(restarted.claim(SECOND).kind == TaskResultLedger.ClaimKind.NEW, "second claim");
        TaskResultLedger interrupted = TaskResultLedger.parse(restarted.encode());
        require(interrupted.recoverInterrupted(), "recover running task");
        TaskResultLedger.Claim recovered = interrupted.claim(SECOND);
        require(recovered.kind == TaskResultLedger.ClaimKind.PENDING, "interrupted result pending");
        require(recovered.pendingMessage.contains("task_interrupted_before_durable_result"), "interrupted code");

        require(interrupted.claim(THIRD).kind == TaskResultLedger.ClaimKind.NEW, "third claim");
        String oversized = "{" + "x".repeat(TaskResultLedger.MAX_RESULT_BYTES + 1) + "}";
        String bounded = interrupted.complete(THIRD, oversized);
        require(bounded.contains("result_too_large_for_outbox"), "oversized fallback");
        require(interrupted.encode().getBytes(java.nio.charset.StandardCharsets.UTF_8).length <=
                TaskResultLedger.MAX_LEDGER_BYTES, "ledger bound");

        boolean corruptRejected = false;
        try {
            TaskResultLedger.parse("not-a-ledger");
        } catch (IllegalStateException expected) {
            corruptRejected = true;
        }
        require(corruptRejected, "corrupt ledger rejected");
        verifyLateCompletionUsesNewAuthenticatedSocket();
        verifyLateCompletionReplayedByHeartbeat();
        verifyReconnectPreflightReasons();
        verifyMonotonicServerClock();
        verifyUtf8ObserveBudget();
        verifyTaskStoreRules();
        verifyPublisherGeneration();
        System.out.println("Task result outbox: PASS (durability, recovery policy, monotonic clock, byte budget, bounds)");
    }

    private static void verifyLateCompletionUsesNewAuthenticatedSocket() {
        Object oldSocket = new Object();
        Object newSocket = new Object();
        Object laterSocket = new Object();
        AuthenticatedSocketGate<Object> gate = new AuthenticatedSocketGate<>();

        require(!gate.isAuthenticated(oldSocket), "task gate closed before auth_ok");
        gate.markAuthenticated(oldSocket);
        Object executionCapturedSocket = oldSocket;

        require(gate.select(newSocket) == null, "new socket must not receive before auth_ok");
        gate.markAuthenticated(newSocket);
        require(gate.isAuthenticated(newSocket), "task gate opens only for auth_ok socket");
        Object selectedForLateResult = gate.select(newSocket);
        require(selectedForLateResult == newSocket, "late result selects current authenticated socket");
        require(selectedForLateResult != executionCapturedSocket, "late result does not use captured old socket");
        require(
                gate.deliveryConfirmed(selectedForLateResult, true, newSocket),
                "current authenticated delivery accepted"
        );

        gate.markAuthenticated(laterSocket);
        require(
                !gate.deliveryConfirmed(selectedForLateResult, true, laterSocket),
                "socket replacement requires outbox reconnect"
        );
        require(
                !gate.deliveryConfirmed(laterSocket, false, laterSocket),
                "pending resend rejection requires outbox reconnect"
        );
    }

    private static void verifyLateCompletionReplayedByHeartbeat() {
        TaskResultLedger sharedStore = TaskResultLedger.empty();
        TaskResultLedger.Claim oldExecution = sharedStore.claim(FIRST);
        require(oldExecution.kind == TaskResultLedger.ClaimKind.NEW,
                "old executor owns durable running claim");

        Object replacementSocket = new Object();
        AuthenticatedSocketGate<Object> replacementGate = new AuthenticatedSocketGate<>();
        replacementGate.markAuthenticated(replacementSocket);
        require(sharedStore.pendingMessages().isEmpty(),
                "replacement auth may precede late result");

        String lateResult = "{\"type\":\"task_result\",\"protocol_version\":\"1\",\"task_id\":\"" +
                FIRST + "\",\"status\":\"succeeded\",\"result\":{\"late\":true}}";
        sharedStore.complete(FIRST, lateResult);
        require(sharedStore.claim(FIRST).kind == TaskResultLedger.ClaimKind.PENDING,
                "late completion is durable and not re-executed");
        require(sharedStore.pendingMessages().size() == 1 &&
                        lateResult.equals(sharedStore.pendingMessages().get(0)),
                "next heartbeat replays exact late result on replacement socket");
        require(replacementGate.select(replacementSocket) == replacementSocket,
                "heartbeat replay selects current authenticated socket");
    }

    private static void verifyReconnectPreflightReasons() {
        require(
                "service_not_running".equals(ReconnectPreflight.evaluate(false, false, true, true, true)),
                "service early return"
        );
        require(
                "device_revoked".equals(ReconnectPreflight.evaluate(true, true, true, true, true)),
                "revoked early return"
        );
        require(
                "cloud_base_url_not_configured".equals(
                        ReconnectPreflight.evaluate(true, false, false, true, true)
                ),
                "config early return"
        );
        require(
                "device_unbound".equals(ReconnectPreflight.evaluate(true, false, true, false, true)),
                "unbound early return"
        );
        require(
                "network_unavailable".equals(ReconnectPreflight.evaluate(true, false, true, true, false)),
                "network early return"
        );
        require(
                "reconnect_ready".equals(ReconnectPreflight.evaluate(true, false, true, true, true)),
                "reconnect ready"
        );
    }

    private static void verifyMonotonicServerClock() {
        TrustedServerClock clock = new TrustedServerClock();
        require(clock.nowSeconds(10L, 900L) == 900L, "wall fallback before auth");
        clock.anchor(1_000L, 5_000L);
        require(clock.nowSeconds(6_000L, 9_999_999L) == 1_001L, "wall forward jump ignored");
        require(clock.nowSeconds(7_000L, 1L) == 1_002L, "wall backward jump ignored");
        require(clock.nowSeconds(4_000L, 9_999_999L) == 1_000L, "elapsed rollback fails closed");
    }

    private static void verifyUtf8ObserveBudget() {
        Utf8JsonBudget budget = new Utf8JsonBudget(6);
        require(budget.tryConsumeJsonArrayEntry("é", false), "two-byte entry accepted");
        require(budget.tryConsumeJsonArrayEntry("a", true), "comma accounted");
        require(!budget.tryConsumeJsonArrayEntry("😀", true), "utf8 overflow rejected");
        require(budget.usedBytes() == 4, "failed entry does not consume budget");
    }

    private static void verifyTaskStoreRules() {
        long total = 8L * 1024L * 1024L;
        int result = 900 * 1024;
        require(TaskStoreRules.canClaim(0, 0, 0, total - result, 8, 4096, total, result),
                "exact final result reservation accepted");
        require(!TaskStoreRules.canClaim(0, 0, 0, total - result + 1L, 8, 4096, total, result),
                "insufficient durable result reservation rejected");
        require(!TaskStoreRules.canClaim(8, 0, 0, 0L, 8, 4096, total, result),
                "scope in-flight cap");
        require(!TaskStoreRules.canClaim(0, 8, 0, 0L, 8, 4096, total, result),
                "global in-flight cap");
        require(!TaskStoreRules.canClaim(0, 0, 4096, 0L, 8, 4096, total, result),
                "global row cap");
        require(TaskStoreRules.sameNonEmptyScope("device-a", "device-a"), "same scope accepted");
        require(!TaskStoreRules.sameNonEmptyScope("device-b", "device-a"), "foreign scope rejected");
        require("device-123".equals(TaskStoreRules.sanitizeDeviceScope("device-123")),
                "bounded scope retained");
        require(TaskStoreRules.sanitizeDeviceScope("x".repeat(81)).isEmpty(),
                "oversized scope omitted");
        require(TaskStoreRules.sanitizeDeviceScope("device\nsecret").isEmpty(),
                "unsafe scope omitted");
        require(!TaskStoreRules.recoveryTimeReached(101L, 100L), "safe time not early");
        require(TaskStoreRules.recoveryTimeReached(101L, 101L), "safe time exact boundary");
        require(TaskStoreRules.normalizeDiagnosticByteSize(-9L) == -1L,
                "unknown diagnostic size keeps sentinel");
        require(TaskStoreRules.normalizeDiagnosticByteSize(0L) == 0L,
                "real empty diagnostic remains zero");
        require(!TaskStoreRules.canRecoverAfterExecutorStop(false, true),
                "live old executor cannot be recovered");
        require(!TaskStoreRules.canRecoverAfterExecutorStop(true, false),
                "non-ready store cannot be recovered");
        require(TaskStoreRules.canRecoverAfterExecutorStop(true, true),
                "terminated executor may durably recover abandoned rows");
        require(TaskStoreRules.shouldRecoverOnPrepare("", "device-a"),
                "cold process may recover persisted running rows");
        require(!TaskStoreRules.shouldRecoverOnPrepare("device-a", "device-a"),
                "same-process reopen cannot re-arm live running recovery");
        require(TaskStoreRules.canAdmitTask(true, true, true),
                "running authenticated client admits before quiesce");
        require(!TaskStoreRules.canAdmitTask(false, true, true),
                "stop closes admission before claim");
        require(!TaskStoreRules.canAdmitTask(true, true, false),
                "shutdown executor cannot receive a new durable claim");
    }

    private static void verifyPublisherGeneration() {
        ActivePublisherGate gate = new ActivePublisherGate();
        ActivePublisherGate.Token retired = gate.activate();
        require(retired.isActive(), "first publisher active");
        ActivePublisherGate.Token current = gate.activate();
        require(!retired.isActive(), "retired publisher cannot overwrite state");
        require(current.isActive(), "latest publisher owns state");
    }

    private static void require(boolean condition, String label) {
        if (!condition) {
            throw new AssertionError(label);
        }
    }
}
