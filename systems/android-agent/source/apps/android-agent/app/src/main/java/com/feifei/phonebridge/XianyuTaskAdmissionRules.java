package com.feifei.phonebridge;

/** Pure admission rules that keep Xianyu work fail-closed outside a verified environment. */
final class XianyuTaskAdmissionRules {
    enum Outcome {
        ALLOW,
        FAIL,
        REJECT
    }

    static final class Decision {
        final Outcome outcome;
        final String code;

        private Decision(Outcome outcome, String code) {
            this.outcome = outcome;
            this.code = code == null ? "" : code;
        }

        static Decision allow() {
            return new Decision(Outcome.ALLOW, "");
        }

        static Decision fail(String code) {
            return new Decision(Outcome.FAIL, code);
        }

        static Decision reject(String code) {
            return new Decision(Outcome.REJECT, code);
        }
    }

    private XianyuTaskAdmissionRules() {
    }

    static Decision snapshot(ExecutionEnvironmentRules.State state) {
        return state == ExecutionEnvironmentRules.State.READY
                ? Decision.allow()
                : Decision.fail(state.code);
    }

    static Decision action(
            ExecutionEnvironmentRules.State state,
            String action,
            String packageName
    ) {
        String normalizedAction = action == null ? "" : action.trim();
        if ("pause".equals(normalizedAction) || "resume".equals(normalizedAction)) {
            return Decision.allow();
        }
        if ("launch_app".equals(normalizedAction)) {
            if (!ExecutionEnvironmentRules.XIANYU_PACKAGE.equals(
                    packageName == null ? "" : packageName.trim())) {
                return Decision.reject("unsupported_launch_package");
            }
            return ExecutionEnvironmentRules.mayLaunchXianyu(state)
                    ? Decision.allow()
                    : Decision.fail(state.code);
        }
        return state == ExecutionEnvironmentRules.State.READY
                ? Decision.allow()
                : Decision.fail(state.code);
    }
}
