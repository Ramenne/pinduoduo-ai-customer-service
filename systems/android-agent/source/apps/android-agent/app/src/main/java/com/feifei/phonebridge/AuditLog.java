package com.feifei.phonebridge;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.Deque;

final class AuditLog {
    private static final long MAX_BYTES = 2L * 1024L * 1024L;
    private static final int MAX_RETURNED = 100;

    private AuditLog() {}

    static synchronized void append(Context context, String action, boolean success, JSONObject detail) {
        try {
            JSONObject line = new JSONObject();
            line.put("at", System.currentTimeMillis());
            line.put("action", action);
            line.put("success", success);
            if (detail != null) {
                line.put("detail", detail);
            }
            File file = file(context);
            if (file.length() > MAX_BYTES) {
                trim(file);
            }
            try (FileOutputStream output = new FileOutputStream(file, true)) {
                output.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) {
            // Audit failure must not crash the control plane.
        }
    }

    static synchronized JSONArray recent(Context context) throws Exception {
        Deque<JSONObject> recent = new ArrayDeque<>(MAX_RETURNED);
        File file = file(context);
        if (!file.exists()) {
            return new JSONArray();
        }
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                new FileInputStream(file), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                try {
                    if (recent.size() == MAX_RETURNED) {
                        recent.removeFirst();
                    }
                    recent.addLast(new JSONObject(line));
                } catch (JSONException ignored) {
                    // Skip truncated records.
                }
            }
        }
        JSONArray array = new JSONArray();
        for (JSONObject entry : recent) {
            array.put(entry);
        }
        return array;
    }

    private static File file(Context context) {
        return new File(context.getFilesDir(), "audit.jsonl");
    }

    private static void trim(File file) throws Exception {
        Deque<String> lines = new ArrayDeque<>();
        int bytes = 0;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                new FileInputStream(file), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                int lineBytes = line.getBytes(StandardCharsets.UTF_8).length + 1;
                lines.addLast(line);
                bytes += lineBytes;
                while (bytes > MAX_BYTES / 2 && !lines.isEmpty()) {
                    String removed = lines.removeFirst();
                    bytes -= removed.getBytes(StandardCharsets.UTF_8).length + 1;
                }
            }
        }
        try (FileOutputStream output = new FileOutputStream(file, false)) {
            for (String line : lines) {
                output.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            }
        }
    }
}
