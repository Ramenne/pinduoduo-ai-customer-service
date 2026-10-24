package com.feifei.phonebridge;

import android.app.KeyguardManager;
import android.content.Context;
import android.os.PowerManager;

import org.json.JSONException;
import org.json.JSONObject;

/** Android adapter for the deterministic Xianyu execution-readiness contract. */
final class XianyuExecutionEnvironment {
    private XianyuExecutionEnvironment() {
    }

    static Snapshot capture(Context context) {
        Context app = context.getApplicationContext();
        boolean screenKnown = false;
        boolean interactive = false;
        boolean lockKnown = false;
        boolean locked = false;
        try {
            PowerManager power = app.getSystemService(PowerManager.class);
            if (power != null) {
                screenKnown = true;
                interactive = power.isInteractive();
            }
            KeyguardManager keyguard = app.getSystemService(KeyguardManager.class);
            if (keyguard != null) {
                lockKnown = true;
                locked = keyguard.isKeyguardLocked();
            }
        } catch (RuntimeException ignored) {
            screenKnown = false;
            lockKnown = false;
        }

        boolean accessibilityKnown = false;
        boolean accessibilityEnabled = false;
        BridgeAccessibilityService service = BridgeAccessibilityService.getInstance();
        boolean accessibilityConnected = false;
        String activePackage = "";
        try {
            accessibilityEnabled = BridgeState.isAccessibilityEnabled(app);
            accessibilityConnected = BridgeState.accessibilityConnected && service != null;
            accessibilityKnown = true;
            if (service != null) {
                activePackage = service.currentActivePackage();
            }
        } catch (RuntimeException ignored) {
            accessibilityKnown = false;
            accessibilityConnected = false;
            activePackage = "";
        }
        ExecutionEnvironmentRules.State state = ExecutionEnvironmentRules.classify(
                screenKnown,
                interactive,
                lockKnown,
                locked,
                accessibilityKnown,
                accessibilityEnabled,
                accessibilityConnected,
                activePackage
        );
        return new Snapshot(
                state,
                screenKnown,
                interactive,
                lockKnown,
                locked,
                accessibilityKnown,
                accessibilityEnabled,
                accessibilityConnected,
                activePackage
        );
    }

    static final class Snapshot {
        final ExecutionEnvironmentRules.State state;
        final boolean screenStateKnown;
        final boolean screenInteractive;
        final boolean lockStateKnown;
        final boolean deviceLocked;
        final boolean accessibilityStateKnown;
        final boolean accessibilityEnabled;
        final boolean accessibilityConnected;
        final String activePackage;

        Snapshot(
                ExecutionEnvironmentRules.State state,
                boolean screenStateKnown,
                boolean screenInteractive,
                boolean lockStateKnown,
                boolean deviceLocked,
                boolean accessibilityStateKnown,
                boolean accessibilityEnabled,
                boolean accessibilityConnected,
                String activePackage
        ) {
            this.state = state;
            this.screenStateKnown = screenStateKnown;
            this.screenInteractive = screenInteractive;
            this.lockStateKnown = lockStateKnown;
            this.deviceLocked = deviceLocked;
            this.accessibilityStateKnown = accessibilityStateKnown;
            this.accessibilityEnabled = accessibilityEnabled;
            this.accessibilityConnected = accessibilityConnected;
            this.activePackage = activePackage == null ? "" : activePackage;
        }

        boolean ready() {
            return state == ExecutionEnvironmentRules.State.READY;
        }

        boolean mayLaunchXianyu() {
            return ExecutionEnvironmentRules.mayLaunchXianyu(state);
        }

        String errorCode() {
            return state.code;
        }

        JSONObject toJson() throws JSONException {
            JSONObject json = new JSONObject();
            json.put("state", state.code);
            json.put("ready", ready());
            json.put("screen_state_known", screenStateKnown);
            json.put("screen_interactive", screenInteractive);
            json.put("lock_state_known", lockStateKnown);
            json.put("device_locked", deviceLocked);
            json.put("accessibility_state_known", accessibilityStateKnown);
            json.put("accessibility_enabled", accessibilityEnabled);
            json.put("accessibility_connected", accessibilityConnected);
            json.put("foreground_package", activePackage);
            return json;
        }
    }
}
