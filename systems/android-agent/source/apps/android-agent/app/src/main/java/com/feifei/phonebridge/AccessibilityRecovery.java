package com.feifei.phonebridge;

import android.app.Activity;
import android.content.Context;
import android.os.Handler;
import android.os.SystemClock;

/** Allow system-managed reconnection without toggling permission or replaying an action. */
final class AccessibilityRecovery {
    static final long WINDOW_MS = 12000L;
    private static long lastRequest = -5000L;
    private AccessibilityRecovery() {}

    static boolean connected() { return BridgeAccessibilityService.getInstance() != null; }

    static synchronized void request(Context context) {
        if (connected() || !BridgeState.isAccessibilityEnabled(context)) return;
        long now = SystemClock.elapsedRealtime();
        if (now - lastRequest < 5000L) return;
        lastRequest = now;
        try {
            // Android exposes no public API to force-rebind an AccessibilityService.
            // Keep our control process alive while the OS restores the enabled binding.
            ControlService.requestStart(context);
        } catch (RuntimeException failure) {
            android.util.Log.w("OIN-Recovery", "Control process recovery request failed", failure);
        }
    }

    // Worker lane only. The caller rechecks screen, pause and foreground before execution.
    static boolean await(Context context, long budgetMs) throws InterruptedException {
        long deadline = SystemClock.elapsedRealtime() + Math.min(WINDOW_MS, Math.max(0L, budgetMs));
        while (!connected() && BridgeState.isAccessibilityEnabled(context) && !BridgeState.PAUSED.get()) {
            if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
            long remaining = deadline - SystemClock.elapsedRealtime();
            if (remaining <= 0) break;
            request(context);
            Thread.sleep(Math.min(250L, remaining));
        }
        return connected();
    }

    // UI lane: lifecycle-bound, no blocking and no resume after the user leaves this page.
    static void await(Activity activity, Handler handler, Runnable ready, Runnable unavailable) {
        long deadline = SystemClock.elapsedRealtime() + WINDOW_MS;
        handler.post(new Runnable() {
            public void run() {
                if (activity.isFinishing() || activity.isDestroyed()) return;
                if (connected()) { ready.run(); return; }
                if (!BridgeState.isAccessibilityEnabled(activity) || SystemClock.elapsedRealtime() >= deadline) {
                    unavailable.run(); return;
                }
                request(activity);
                handler.postDelayed(this, 250L);
            }
        });
    }
}
