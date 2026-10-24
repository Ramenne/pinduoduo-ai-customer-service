package com.feifei.phonebridge;

/** Pure execution-readiness rules shared by Android adapters and JVM contract tests. */
final class ExecutionEnvironmentRules {
    static final String XIANYU_PACKAGE = "com.taobao.idlefish";
    static final String SYSTEM_UI_PACKAGE = "com.android.systemui";

    enum State {
        READY("ready"),
        SCREEN_OFF("screen_off"),
        DEVICE_LOCKED("device_locked"),
        ACCESSIBILITY_UNAVAILABLE("accessibility_unavailable"),
        SYSTEM_UI_FOREGROUND("system_ui_foreground"),
        XIANYU_NOT_FOREGROUND("xianyu_not_foreground"),
        UNKNOWN("execution_environment_unknown");

        final String code;

        State(String code) {
            this.code = code;
        }
    }

    private ExecutionEnvironmentRules() {
    }

    static State classify(
            boolean screenStateKnown,
            boolean screenInteractive,
            boolean lockStateKnown,
            boolean deviceLocked,
            boolean accessibilityStateKnown,
            boolean accessibilityEnabled,
            boolean accessibilityConnected,
            String activePackage
    ) {
        if (!screenStateKnown) {
            return State.UNKNOWN;
        }
        if (!screenInteractive) {
            return State.SCREEN_OFF;
        }
        if (!lockStateKnown) {
            return State.UNKNOWN;
        }
        if (deviceLocked) {
            return State.DEVICE_LOCKED;
        }
        if (!accessibilityStateKnown) {
            return State.UNKNOWN;
        }
        if (!accessibilityEnabled || !accessibilityConnected) {
            return State.ACCESSIBILITY_UNAVAILABLE;
        }
        String normalizedPackage = activePackage == null ? "" : activePackage.trim();
        if (normalizedPackage.isEmpty()) {
            return State.UNKNOWN;
        }
        if (XIANYU_PACKAGE.equals(normalizedPackage)) {
            return State.READY;
        }
        if (SYSTEM_UI_PACKAGE.equals(normalizedPackage)) {
            return State.SYSTEM_UI_FOREGROUND;
        }
        return State.XIANYU_NOT_FOREGROUND;
    }

    static boolean mayLaunchXianyu(State state) {
        return state == State.READY ||
                state == State.SYSTEM_UI_FOREGROUND ||
                state == State.XIANYU_NOT_FOREGROUND;
    }
}
