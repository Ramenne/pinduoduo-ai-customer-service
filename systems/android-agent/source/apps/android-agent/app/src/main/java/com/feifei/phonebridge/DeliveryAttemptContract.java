package com.feifei.phonebridge;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/** Exact code28 delivery-attempt envelope and ACK contract. */
final class DeliveryAttemptContract {
    static final Set<String> TASK_FIELDS = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
            "type",
            "protocol_version",
            "task_id",
            "task_type",
            "issued_at",
            "expires_at",
            "parameters",
            "delivery_attempt_id"
    )));
    static final Set<String> ACK_FIELDS = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
            "type",
            "protocol_version",
            "task_id",
            "delivery_attempt_id"
    )));

    private DeliveryAttemptContract() {
    }

    static String canonicalAttemptId(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty() || value.length() > 80) {
            throw new IllegalArgumentException("invalid_delivery_attempt_id");
        }
        try {
            if (!UUID.fromString(value).toString().equals(value)) {
                throw new IllegalArgumentException("invalid_delivery_attempt_id");
            }
            return value;
        } catch (IllegalArgumentException invalid) {
            throw new IllegalArgumentException("invalid_delivery_attempt_id", invalid);
        }
    }
}
