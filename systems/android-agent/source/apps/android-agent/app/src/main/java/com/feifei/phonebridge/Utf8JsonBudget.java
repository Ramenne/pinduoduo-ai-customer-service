package com.feifei.phonebridge;

import java.nio.charset.StandardCharsets;

/** Incremental UTF-8 budget used before adding serialized JSON array entries. */
final class Utf8JsonBudget {
    private final int limitBytes;
    private int usedBytes;

    Utf8JsonBudget(int limitBytes) {
        if (limitBytes <= 0) {
            throw new IllegalArgumentException("invalid_utf8_budget");
        }
        this.limitBytes = limitBytes;
    }

    boolean tryConsumeJsonArrayEntry(String encodedEntry, boolean needsComma) {
        if (encodedEntry == null) {
            return false;
        }
        int separatorBytes = needsComma ? 1 : 0;
        if (encodedEntry.length() > limitBytes - usedBytes - separatorBytes) {
            return false;
        }
        int entryBytes = utf8Bytes(encodedEntry);
        if (entryBytes > limitBytes - usedBytes - separatorBytes) {
            return false;
        }
        usedBytes += separatorBytes + entryBytes;
        return true;
    }

    int usedBytes() {
        return usedBytes;
    }

    static int utf8Bytes(String value) {
        return value == null ? 0 : value.getBytes(StandardCharsets.UTF_8).length;
    }
}
