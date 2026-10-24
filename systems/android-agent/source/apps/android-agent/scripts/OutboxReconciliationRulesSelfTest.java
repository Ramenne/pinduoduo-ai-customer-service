package com.feifei.phonebridge;

import java.util.LinkedHashMap;
import java.util.Map;

public final class OutboxReconciliationRulesSelfTest {
    private static final String TASK_ID = "11111111-1111-4111-8111-111111111111";

    private OutboxReconciliationRulesSelfTest() {
    }

    public static void main(String[] args) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("late", true);
        result.put("source", "durable-old-scope");
        Map<String, Object> message = new LinkedHashMap<>();
        message.put("type", "task_result");
        message.put("protocol_version", "1");
        message.put("task_id", TASK_ID);
        message.put("status", "succeeded");
        message.put("result", result);

        OutboxReconciliationRules.ResultSummary summary =
                OutboxReconciliationRules.summarize(TASK_ID, message);
        assertEquals(
                "7ae94a1dd6ec21920b5909a7ff5026f2dcf9e5ea07efe2ffccc5f01e0380107b",
                summary.sha256,
                "Android summary must match the Python canonical result vector"
        );
        assertEquals(74, summary.byteSize, "canonical UTF-8 byte count must be exact");
        assertTrue(OutboxReconciliationRules.validDisposition(
                "admin_revoked_with_late_result"
        ), "late persisted result is an allowed archive disposition");
        assertFalse(OutboxReconciliationRules.validDisposition("purged"),
                "arbitrary purge is not an archive disposition");
        assertTrue(OutboxReconciliationRules.authorizationComplete(false),
                "an exact old scope can complete even while another old scope awaits authorization");
        assertFalse(OutboxReconciliationRules.channelReady(true),
                "the new channel remains blocked while any other foreign scope is nonterminal");

        message.put("unexpected", "must fail closed");
        boolean rejected = false;
        try {
            OutboxReconciliationRules.summarize(TASK_ID, message);
        } catch (IllegalArgumentException expected) {
            rejected = true;
        }
        assertTrue(rejected, "unknown result fields must be rejected");
        System.out.println("Outbox reconciliation rules self-test: PASS");
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }

    private static void assertFalse(boolean value, String message) {
        assertTrue(!value, message);
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }
}
