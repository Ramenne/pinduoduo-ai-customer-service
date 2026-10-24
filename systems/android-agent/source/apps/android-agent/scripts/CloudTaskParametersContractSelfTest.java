package com.feifei.phonebridge;

import java.util.Collections;
import java.util.Set;

public final class CloudTaskParametersContractSelfTest {
    private CloudTaskParametersContractSelfTest() {
    }

    public static void main(String[] args) {
        requireValid("health", Collections.emptySet(), "", "");
        requireError("parameters_not_allowed", "observe", Set.of("extra"), "", "");
        requireValid(
                "action",
                Set.of("action", "package"),
                "launch_app",
                "com.taobao.idlefish"
        );
        requireError(
                "cloud_launch_package_not_allowed",
                "action",
                Set.of("action", "package"),
                "launch_app",
                "com.example.other"
        );
        requireError(
                "invalid_launch_app_parameters",
                "action",
                Set.of("action", "package", "extra"),
                "launch_app",
                "com.taobao.idlefish"
        );
        requireValid("action", Set.of("action"), "back", "");
        requireValid("action", Set.of("action"), "home", "");
        requireValidClick("text", "消息");
        requireValidClick("resource_id", "com.taobao.idlefish:id/tab_message");
        requireValidScroll("up");
        requireValidScroll("down");
        requireErrorScroll(
                "cloud_scroll_package_not_allowed",
                Set.of("action", "package", "direction"),
                "com.example.other",
                "up"
        );
        requireErrorScroll(
                "invalid_scroll_direction",
                Set.of("action", "package", "direction"),
                "com.taobao.idlefish",
                "left"
        );
        requireErrorScroll(
                "invalid_scroll_direction",
                Set.of("action", "package", "direction"),
                "com.taobao.idlefish",
                Integer.valueOf(1)
        );
        requireErrorScroll(
                "invalid_scroll_page_parameters",
                Set.of("action", "package", "direction", "distance"),
                "com.taobao.idlefish",
                "up"
        );
        requireErrorClick("cloud_click_package_not_allowed", "com.example.other",
                true, Set.of("kind", "value"), "text", "消息");
        requireErrorClick("invalid_node_selector", "com.taobao.idlefish",
                true, Set.of("kind", "value", "contains"), "text", "消息");
        requireErrorClick("invalid_node_selector_kind", "com.taobao.idlefish",
                true, Set.of("kind", "value"), "bounds", "0,0,1,1");
        requireErrorClick("invalid_node_selector_value", "com.taobao.idlefish",
                true, Set.of("kind", "value"), "text", "   ");
        requireErrorClick("invalid_node_selector_value", "com.taobao.idlefish",
                true, Set.of("kind", "value"), "text", Integer.valueOf(1));
        if (!XianyuNodeSelector.matches(
                "text", "消息", "", " 消息 ", "", false)) {
            throw new AssertionError("NFKC/trim exact text must match");
        }
        if (XianyuNodeSelector.matches(
                "text", "消息", "", "消息中心", "", false)) {
            throw new AssertionError("contains text must not match");
        }
        if (!XianyuNodeSelector.matches(
                "description", "消息", "", "", "消息", false)) {
            throw new AssertionError("description selector must use description");
        }
        if (!VisibleNodeSearchPolicy.shouldTraverse(true, false)) {
            throw new AssertionError("root must be traversed to reach visible descendants");
        }
        if (VisibleNodeSearchPolicy.shouldTraverse(false, false)) {
            throw new AssertionError("hidden non-root subtree must be pruned");
        }
        if (!VisibleNodeSearchPolicy.canMatch(true) || VisibleNodeSearchPolicy.canMatch(false)) {
            throw new AssertionError("only visible nodes may match an action selector");
        }
        if (!VisibleNodeSearchPolicy.canDispatch(true, true)
                || VisibleNodeSearchPolicy.canDispatch(true, false)
                || VisibleNodeSearchPolicy.canDispatch(false, true)) {
            throw new AssertionError("click dispatch requires a visible enabled target");
        }
        requireError(
                "unsupported_cloud_action",
                "action",
                Set.of("action", "text"),
                "click_text",
                ""
        );
        System.out.println("CLOUD_TASK_PARAMETERS_CONTRACT_SELF_TEST_PASS");
    }

    private static void requireValidClick(String kind, String value) {
        String result = CloudTaskParametersContract.validate(
                "action",
                Set.of("action", "package", "selector"),
                "click_node",
                "com.taobao.idlefish",
                true,
                Set.of("kind", "value"),
                kind,
                value
        );
        if (result != null) {
            throw new AssertionError("expected valid click but got " + result);
        }
    }

    private static void requireErrorClick(
            String expected,
            String packageName,
            boolean selectorObject,
            Set<String> selectorFields,
            Object kind,
            Object value
    ) {
        String actual = CloudTaskParametersContract.validate(
                "action",
                Set.of("action", "package", "selector"),
                "click_node",
                packageName,
                selectorObject,
                selectorFields,
                kind,
                value
        );
        if (!expected.equals(actual)) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }

    private static void requireValidScroll(String direction) {
        String result = CloudTaskParametersContract.validate(
                "action",
                Set.of("action", "package", "direction"),
                "scroll_page",
                "com.taobao.idlefish",
                false,
                Collections.emptySet(),
                null,
                null,
                direction
        );
        if (result != null) {
            throw new AssertionError("expected valid scroll but got " + result);
        }
    }

    private static void requireErrorScroll(
            String expected,
            Set<String> fields,
            String packageName,
            Object direction
    ) {
        String actual = CloudTaskParametersContract.validate(
                "action",
                fields,
                "scroll_page",
                packageName,
                false,
                Collections.emptySet(),
                null,
                null,
                direction
        );
        if (!expected.equals(actual)) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }

    private static void requireValid(
            String taskType,
            Set<String> fields,
            String action,
            String packageName
    ) {
        String result = CloudTaskParametersContract.validate(
                taskType, fields, action, packageName);
        if (result != null) {
            throw new AssertionError("expected valid but got " + result);
        }
    }

    private static void requireError(
            String expected,
            String taskType,
            Set<String> fields,
            String action,
            String packageName
    ) {
        String actual = CloudTaskParametersContract.validate(
                taskType, fields, action, packageName);
        if (!expected.equals(actual)) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }
}
