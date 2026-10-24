package com.feifei.phonebridge;

public final class XianyuTaskAdmissionRulesSelfTest {
    private XianyuTaskAdmissionRulesSelfTest() {
    }

    public static void main(String[] args) {
        expect(XianyuTaskAdmissionRules.Outcome.ALLOW,
                XianyuTaskAdmissionRules.snapshot(ExecutionEnvironmentRules.State.READY));
        expectCode(XianyuTaskAdmissionRules.Outcome.FAIL, "system_ui_foreground",
                XianyuTaskAdmissionRules.snapshot(ExecutionEnvironmentRules.State.SYSTEM_UI_FOREGROUND));
        expectCode(XianyuTaskAdmissionRules.Outcome.FAIL, "device_locked",
                XianyuTaskAdmissionRules.action(ExecutionEnvironmentRules.State.DEVICE_LOCKED,
                        "click", ""));
        expect(XianyuTaskAdmissionRules.Outcome.ALLOW,
                XianyuTaskAdmissionRules.action(ExecutionEnvironmentRules.State.XIANYU_NOT_FOREGROUND,
                        "launch_app", ExecutionEnvironmentRules.XIANYU_PACKAGE));
        expectCode(XianyuTaskAdmissionRules.Outcome.REJECT, "unsupported_launch_package",
                XianyuTaskAdmissionRules.action(ExecutionEnvironmentRules.State.XIANYU_NOT_FOREGROUND,
                        "launch_app", "com.miui.home"));
        expectCode(XianyuTaskAdmissionRules.Outcome.FAIL, "screen_off",
                XianyuTaskAdmissionRules.action(ExecutionEnvironmentRules.State.SCREEN_OFF,
                        "launch_app", ExecutionEnvironmentRules.XIANYU_PACKAGE));
        expect(XianyuTaskAdmissionRules.Outcome.ALLOW,
                XianyuTaskAdmissionRules.action(ExecutionEnvironmentRules.State.UNKNOWN,
                        "pause", ""));
        expect(XianyuTaskAdmissionRules.Outcome.ALLOW,
                XianyuTaskAdmissionRules.action(ExecutionEnvironmentRules.State.DEVICE_LOCKED,
                        "resume", ""));
        System.out.println("Xianyu task admission rules self-test PASS");
    }

    private static void expect(
            XianyuTaskAdmissionRules.Outcome expected,
            XianyuTaskAdmissionRules.Decision actual
    ) {
        if (actual.outcome != expected) {
            throw new AssertionError("expected=" + expected + " actual=" + actual.outcome);
        }
    }

    private static void expectCode(
            XianyuTaskAdmissionRules.Outcome expectedOutcome,
            String expectedCode,
            XianyuTaskAdmissionRules.Decision actual
    ) {
        expect(expectedOutcome, actual);
        if (!expectedCode.equals(actual.code)) {
            throw new AssertionError("expectedCode=" + expectedCode + " actual=" + actual.code);
        }
    }
}
