package com.feifei.phonebridge;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

final class TaskResultLedger {
    static final int MAX_RESULT_BYTES = 900 * 1024;
    static final int MAX_LEDGER_BYTES = 8 * 1024 * 1024;
    static final int MAX_IN_FLIGHT = 8;
    static final int MAX_COMPLETED = 256;
    private static final String HEADER = "OIN_TASK_RESULT_LEDGER_V1";

    enum ClaimKind {
        NEW,
        PENDING,
        RUNNING,
        COMPLETED,
        CAPACITY,
        DEADLINE_EXPIRED
    }

    static final class Claim {
        final ClaimKind kind;
        final String pendingMessage;
        final String deviceScope;

        Claim(ClaimKind kind, String pendingMessage) {
            this(kind, pendingMessage, "");
        }

        Claim(ClaimKind kind, String pendingMessage, String deviceScope) {
            this.kind = kind;
            this.pendingMessage = pendingMessage;
            this.deviceScope = deviceScope == null ? "" : deviceScope;
        }
    }

    enum RecordState {
        RUNNING,
        PENDING,
        COMPLETED
    }

    static final class Record {
        final String taskId;
        final RecordState state;
        final String resultMessage;

        Record(String taskId, RecordState state, String resultMessage) {
            this.taskId = taskId;
            this.state = state;
            this.resultMessage = resultMessage;
        }
    }

    private final Set<String> running = new LinkedHashSet<>();
    private final Map<String, String> pending = new LinkedHashMap<>();
    private final Set<String> completed = new LinkedHashSet<>();

    static TaskResultLedger empty() {
        return new TaskResultLedger();
    }

    static TaskResultLedger parse(String encoded) {
        if (encoded == null || encoded.isEmpty()) {
            return empty();
        }
        if (encoded.length() > MAX_LEDGER_BYTES ||
                encoded.getBytes(StandardCharsets.UTF_8).length > MAX_LEDGER_BYTES) {
            throw new IllegalStateException("task_result_ledger_too_large");
        }
        String[] lines = encoded.split("\n", -1);
        if (lines.length < 2 || !HEADER.equals(lines[0]) || !lines[lines.length - 1].isEmpty()) {
            throw new IllegalStateException("task_result_ledger_corrupt");
        }
        TaskResultLedger ledger = empty();
        for (int index = 1; index < lines.length - 1; index++) {
            String line = lines[index];
            String[] parts = line.split("\t", 3);
            if (parts.length < 2) {
                throw new IllegalStateException("task_result_ledger_corrupt");
            }
            String taskId = requireTaskId(parts[1]);
            if (ledger.contains(taskId)) {
                throw new IllegalStateException("task_result_ledger_duplicate_id");
            }
            switch (parts[0]) {
                case "R":
                    if (parts.length != 2) {
                        throw new IllegalStateException("task_result_ledger_corrupt");
                    }
                    ledger.running.add(taskId);
                    break;
                case "P":
                    if (parts.length != 3 || !isValidMessage(parts[2])) {
                        throw new IllegalStateException("task_result_ledger_corrupt");
                    }
                    ledger.pending.put(taskId, parts[2]);
                    break;
                case "C":
                    if (parts.length != 2) {
                        throw new IllegalStateException("task_result_ledger_corrupt");
                    }
                    ledger.completed.add(taskId);
                    break;
                default:
                    throw new IllegalStateException("task_result_ledger_corrupt");
            }
        }
        if (ledger.running.size() + ledger.pending.size() > MAX_IN_FLIGHT ||
                ledger.completed.size() > MAX_COMPLETED) {
            throw new IllegalStateException("task_result_ledger_limits_exceeded");
        }
        return ledger;
    }

    Claim claim(String taskId) {
        taskId = requireTaskId(taskId);
        String pendingMessage = pending.get(taskId);
        if (pendingMessage != null) {
            return new Claim(ClaimKind.PENDING, pendingMessage);
        }
        if (running.contains(taskId)) {
            return new Claim(ClaimKind.RUNNING, null);
        }
        if (completed.contains(taskId)) {
            return new Claim(ClaimKind.COMPLETED, null);
        }
        if (running.size() + pending.size() >= MAX_IN_FLIGHT) {
            return new Claim(ClaimKind.CAPACITY, null);
        }
        running.add(taskId);
        return new Claim(ClaimKind.NEW, null);
    }

    String complete(String taskId, String resultMessage) {
        taskId = requireTaskId(taskId);
        if (pending.containsKey(taskId)) {
            return pending.get(taskId);
        }
        if (!running.remove(taskId)) {
            throw new IllegalStateException("task_not_running");
        }
        String stored = resultMessage;
        if (!isValidMessage(stored)) {
            stored = failureMessage(taskId, "result_too_large_for_outbox");
        }
        pending.put(taskId, stored);
        encode();
        return stored;
    }

    boolean recoverInterrupted() {
        if (running.isEmpty()) {
            return false;
        }
        List<String> interrupted = new ArrayList<>(running);
        running.clear();
        for (String taskId : interrupted) {
            pending.put(taskId, failureMessage(taskId, "task_interrupted_before_durable_result"));
        }
        encode();
        return true;
    }

    boolean acknowledge(String taskId) {
        taskId = requireTaskId(taskId);
        if (pending.remove(taskId) == null) {
            return completed.contains(taskId);
        }
        completed.add(taskId);
        while (completed.size() > MAX_COMPLETED) {
            String oldest = completed.iterator().next();
            completed.remove(oldest);
        }
        return true;
    }

    List<String> pendingMessages() {
        return new ArrayList<>(pending.values());
    }

    List<Record> records() {
        List<Record> records = new ArrayList<>(running.size() + pending.size() + completed.size());
        for (String taskId : running) {
            records.add(new Record(taskId, RecordState.RUNNING, null));
        }
        for (Map.Entry<String, String> entry : pending.entrySet()) {
            records.add(new Record(entry.getKey(), RecordState.PENDING, entry.getValue()));
        }
        for (String taskId : completed) {
            records.add(new Record(taskId, RecordState.COMPLETED, null));
        }
        return records;
    }

    String encode() {
        StringBuilder builder = new StringBuilder(HEADER).append('\n');
        for (String taskId : running) {
            builder.append("R\t").append(taskId).append('\n');
        }
        for (Map.Entry<String, String> entry : pending.entrySet()) {
            builder.append("P\t").append(entry.getKey()).append('\t')
                    .append(entry.getValue()).append('\n');
        }
        for (String taskId : completed) {
            builder.append("C\t").append(taskId).append('\n');
        }
        String encoded = builder.toString();
        if (encoded.length() > MAX_LEDGER_BYTES ||
                encoded.getBytes(StandardCharsets.UTF_8).length > MAX_LEDGER_BYTES) {
            throw new IllegalStateException("task_result_ledger_too_large");
        }
        return encoded;
    }

    private boolean contains(String taskId) {
        return running.contains(taskId) || pending.containsKey(taskId) || completed.contains(taskId);
    }

    private static boolean isValidMessage(String message) {
        if (message == null || message.isEmpty() || message.indexOf('\n') >= 0 || message.indexOf('\r') >= 0) {
            return false;
        }
        if (message.length() > MAX_RESULT_BYTES) {
            return false;
        }
        int bytes = message.getBytes(StandardCharsets.UTF_8).length;
        return bytes <= MAX_RESULT_BYTES && message.charAt(0) == '{' &&
                message.charAt(message.length() - 1) == '}';
    }

    private static String failureMessage(String taskId, String code) {
        return "{\"type\":\"task_result\",\"protocol_version\":\"1\",\"task_id\":\"" +
                taskId + "\",\"status\":\"failed\",\"error\":{\"code\":\"" + code +
                "\",\"message\":\"Task result recovered by durable outbox contract\"}}";
    }

    private static String requireTaskId(String taskId) {
        if (taskId == null || !UUID.fromString(taskId).toString().equals(taskId)) {
            throw new IllegalArgumentException("invalid_task_id");
        }
        return taskId;
    }
}
