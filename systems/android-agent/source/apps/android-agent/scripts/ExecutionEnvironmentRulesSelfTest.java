package com.feifei.phonebridge;

public final class ExecutionEnvironmentRulesSelfTest {
    private ExecutionEnvironmentRulesSelfTest() {
    }

    public static void main(String[] args) {
        expect(ExecutionEnvironmentRules.State.UNKNOWN,
                ExecutionEnvironmentRules.classify(false, false, true, false, true, true, true, "com.taobao.idlefish"));
        expect(ExecutionEnvironmentRules.State.SCREEN_OFF,
                ExecutionEnvironmentRules.classify(true, false, true, true, true, true, true, "com.android.systemui"));
        expect(ExecutionEnvironmentRules.State.SCREEN_OFF,
                ExecutionEnvironmentRules.classify(true, false, false, false, false, false, false, ""));
        expect(ExecutionEnvironmentRules.State.DEVICE_LOCKED,
                ExecutionEnvironmentRules.classify(true, true, true, true, true, true, true, "com.android.systemui"));
        expect(ExecutionEnvironmentRules.State.ACCESSIBILITY_UNAVAILABLE,
                ExecutionEnvironmentRules.classify(true, true, true, false, true, true, false, "com.taobao.idlefish"));
        expect(ExecutionEnvironmentRules.State.UNKNOWN,
                ExecutionEnvironmentRules.classify(true, true, true, false, false, true, true, "com.taobao.idlefish"));
        expect(ExecutionEnvironmentRules.State.UNKNOWN,
                ExecutionEnvironmentRules.classify(true, true, true, false, true, true, true, ""));
        expect(ExecutionEnvironmentRules.State.SYSTEM_UI_FOREGROUND,
                ExecutionEnvironmentRules.classify(true, true, true, false, true, true, true, "com.android.systemui"));
        expect(ExecutionEnvironmentRules.State.XIANYU_NOT_FOREGROUND,
                ExecutionEnvironmentRules.classify(true, true, true, false, true, true, true, "com.miui.home"));
        expect(ExecutionEnvironmentRules.State.READY,
                ExecutionEnvironmentRules.classify(true, true, true, false, true, true, true, "com.taobao.idlefish"));

        require(!ExecutionEnvironmentRules.mayLaunchXianyu(ExecutionEnvironmentRules.State.SCREEN_OFF));
        require(!ExecutionEnvironmentRules.mayLaunchXianyu(ExecutionEnvironmentRules.State.DEVICE_LOCKED));
        require(ExecutionEnvironmentRules.mayLaunchXianyu(ExecutionEnvironmentRules.State.SYSTEM_UI_FOREGROUND));
        require(ExecutionEnvironmentRules.mayLaunchXianyu(ExecutionEnvironmentRules.State.XIANYU_NOT_FOREGROUND));
        System.out.println("Execution environment rules self-test PASS");
    }

    private static void expect(
            ExecutionEnvironmentRules.State expected,
            ExecutionEnvironmentRules.State actual
    ) {
        if (expected != actual) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }

    private static void require(boolean condition) {
        if (!condition) {
            throw new AssertionError("condition failed");
        }
    }
}
