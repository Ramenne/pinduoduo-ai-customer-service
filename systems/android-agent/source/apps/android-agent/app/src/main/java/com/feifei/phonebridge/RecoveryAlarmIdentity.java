package com.feifei.phonebridge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/** Stable PendingIntent identity; extras remain payload and never define alarm ownership. */
final class RecoveryAlarmIdentity {
    private RecoveryAlarmIdentity() {
    }

    static String dataUri(long generation, String processInstanceId, String phase) {
        return "oin-recovery://guardian/g/" + generation + "/p/" + shortDigest(
                processInstanceId == null ? "" : processInstanceId) + "/phase/" + safePhase(phase);
    }

    private static String safePhase(String phase) {
        if ("preclaim".equals(phase) || "handoff".equals(phase)) {
            return phase;
        }
        return "liveness";
    }

    private static String shortDigest(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(16);
            for (int index = 0; index < 8; index++) {
                hex.append(Character.forDigit((digest[index] >>> 4) & 0xF, 16));
                hex.append(Character.forDigit(digest[index] & 0xF, 16));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("sha256_unavailable", impossible);
        }
    }
}
