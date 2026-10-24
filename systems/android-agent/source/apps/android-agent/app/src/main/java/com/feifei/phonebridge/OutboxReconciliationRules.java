package com.feifei.phonebridge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Android-free canonical summary rules shared by the foreign-outbox protocol. */
final class OutboxReconciliationRules {
    static final class ResultSummary {
        final String sha256;
        final int byteSize;

        ResultSummary(String sha256, int byteSize) {
            this.sha256 = sha256;
            this.byteSize = byteSize;
        }
    }

    private OutboxReconciliationRules() {
    }

    static ResultSummary summarize(String expectedTaskId, Map<String, Object> message) {
        if (message == null || !"task_result".equals(message.get("type")) ||
                !"1".equals(message.get("protocol_version")) ||
                !expectedTaskId.equals(message.get("task_id"))) {
            throw new IllegalArgumentException("invalid_reconciliation_result_envelope");
        }
        Object rawStatus = message.get("status");
        if (!(rawStatus instanceof String)) {
            throw new IllegalArgumentException("invalid_reconciliation_result_status");
        }
        String status = (String) rawStatus;
        Map<String, Object> resultEnvelope = new LinkedHashMap<>();
        resultEnvelope.put("status", status);
        if ("succeeded".equals(status)) {
            requireExactFields(message, "type", "protocol_version", "task_id", "status", "result");
            resultEnvelope.put("result", message.get("result"));
        } else if ("failed".equals(status) || "rejected".equals(status)) {
            requireExactFields(message, "type", "protocol_version", "task_id", "status", "error");
            Object rawError = message.get("error");
            if (!(rawError instanceof Map)) {
                throw new IllegalArgumentException("invalid_reconciliation_result_error");
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> error = (Map<String, Object>) rawError;
            requireExactFields(error, "code", "message");
            if (!(error.get("code") instanceof String) || !(error.get("message") instanceof String)) {
                throw new IllegalArgumentException("invalid_reconciliation_result_error");
            }
            resultEnvelope.put("error", error);
        } else {
            throw new IllegalArgumentException("invalid_reconciliation_result_status");
        }
        byte[] bytes = CanonicalJsonWriter.write(resultEnvelope).getBytes(StandardCharsets.UTF_8);
        return new ResultSummary(sha256(bytes), bytes.length);
    }

    static boolean validDisposition(String value) {
        return "expired_with_late_result".equals(value) ||
                "admin_revoked_with_late_result".equals(value) ||
                "admin_revoked_interrupted".equals(value);
    }

    static boolean authorizationComplete(boolean exactOldScopeHasNonterminal) {
        return !exactOldScopeHasNonterminal;
    }

    static boolean channelReady(boolean anyForeignScopeHasNonterminal) {
        return !anyForeignScopeHasNonterminal;
    }

    private static void requireExactFields(Map<String, Object> value, String... fields) {
        Set<String> expected = new HashSet<>(Arrays.asList(fields));
        if (!value.keySet().equals(expected)) {
            throw new IllegalArgumentException("invalid_reconciliation_result_fields");
        }
    }

    private static String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder value = new StringBuilder(64);
            for (byte item : digest) {
                value.append(String.format(java.util.Locale.ROOT, "%02x", item & 0xff));
            }
            return value.toString();
        } catch (Exception unavailable) {
            throw new IllegalStateException("sha256_unavailable", unavailable);
        }
    }
}
