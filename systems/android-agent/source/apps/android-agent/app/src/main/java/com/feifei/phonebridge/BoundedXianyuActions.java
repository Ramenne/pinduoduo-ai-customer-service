package com.feifei.phonebridge;

import java.util.Set;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;

/** New actions use literal equality, never the legacy click_node normalization. */
final class BoundedXianyuActions {
    static final String PACKAGE = "com.taobao.idlefish";
    static final int MAX_TEXT_CHARS = 5000;
    private static final Set<String> SELECTOR_FIELDS = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList("kind", "value")));
    private static final Set<String> KINDS = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList("resource_id", "text", "description", "class")));

    private BoundedXianyuActions() {}

    static String validateSelector(boolean object, Set<String> fields, Object kind, Object value) {
        if (!object || !SELECTOR_FIELDS.equals(fields)) return "invalid_node_selector";
        if (!(kind instanceof String) ||
                !KINDS.contains(kind)) {
            return "invalid_node_selector_kind";
        }
        if (!(value instanceof String) || ((String) value).trim().isEmpty() ||
                ((String) value).length() > XianyuNodeSelector.MAX_VALUE_CHARS) {
            return "invalid_node_selector_value";
        }
        return null;
    }

    static String validateText(Object value) {
        return value instanceof String && ((String) value).length() >= 1 &&
                ((String) value).length() <= MAX_TEXT_CHARS ? null : "invalid_node_text_value";
    }

    static boolean matches(String kind, String value, String id, CharSequence text,
                           CharSequence description, boolean password) {
        if (password) return false;
        switch (kind) {
            case "resource_id": return value.equals(id);
            case "text": return text != null && value.contentEquals(text);
            case "description": return description != null && value.contentEquals(description);
            default: return false;
        }
    }

    static boolean maySet(boolean visible, boolean enabled, boolean editable, boolean password) {
        return visible && enabled && editable && !password;
    }
}
