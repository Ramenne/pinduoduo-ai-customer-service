package com.feifei.phonebridge;

import android.app.ActivityManager;
import android.app.ApplicationExitInfo;
import android.content.Context;
import android.os.Build;

import org.json.JSONObject;

import java.util.List;

/** Captures the previous process exit reason when Android exposes it (API 30+). */
final class ProcessExitDiagnostics {
    private ProcessExitDiagnostics() {
    }

    static JSONObject previousExit(Context context) {
        JSONObject result = new JSONObject();
        try {
            if (Build.VERSION.SDK_INT < 30) {
                return result.put("available", false).put("reason", "api_below_30");
            }
            ActivityManager manager = context.getSystemService(ActivityManager.class);
            if (manager == null) {
                return result.put("available", false).put("reason", "activity_manager_unavailable");
            }
            List<ApplicationExitInfo> exits = manager.getHistoricalProcessExitReasons(
                    context.getPackageName(), 0, 2);
            if (exits == null || exits.isEmpty()) {
                return result.put("available", true).put("found", false);
            }
            ApplicationExitInfo exit = exits.get(0);
            return result.put("available", true)
                    .put("found", true)
                    .put("reason_code", exit.getReason())
                    .put("status", exit.getStatus())
                    .put("importance", exit.getImportance())
                    .put("timestamp", exit.getTimestamp())
                    .put("description", safe(exit.getDescription()));
        } catch (Throwable failure) {
            try {
                return result.put("available", false).put("reason", "exit_info_read_failed");
            } catch (Exception ignored) {
                return new JSONObject();
            }
        }
    }

    private static String safe(String value) {
        String normalized = value == null ? "" : value.trim();
        return normalized.length() <= 160 ? normalized : normalized.substring(0, 160);
    }
}
