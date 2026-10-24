package com.feifei.phonebridge;

import android.app.Application;
import android.content.Context;
import android.content.SharedPreferences;

/**
 * Main-process-only user choices.
 *
 * <p>Android {@link SharedPreferences} does not provide a coherent multi-process cache. The
 * out-of-process recovery guardian must use {@link RecoveryLeaseStore} as its only authority and
 * must never read this class.</p>
 */
final class AgentRuntimePreferences {
    private static final String PREFS = "oin_agent_runtime_preferences";
    private static final String AUTO_CONNECT = "auto_connect";

    private AgentRuntimePreferences() {
    }

    static boolean autoConnectEnabled(Context context) {
        requireMainProcess(context);
        return preferences(context).getBoolean(AUTO_CONNECT, true);
    }

    static boolean setAutoConnectEnabled(Context context, boolean enabled) {
        requireMainProcess(context);
        SharedPreferences preferences = preferences(context);
        return preferences.edit().putBoolean(AUTO_CONNECT, enabled).commit() &&
                preferences.getBoolean(AUTO_CONNECT, !enabled) == enabled;
    }

    private static void requireMainProcess(Context context) {
        Context app = context.getApplicationContext();
        String processName = Application.getProcessName();
        if (processName == null || !app.getPackageName().equals(processName)) {
            throw new IllegalStateException("runtime_preferences_main_process_only");
        }
    }

    private static SharedPreferences preferences(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
