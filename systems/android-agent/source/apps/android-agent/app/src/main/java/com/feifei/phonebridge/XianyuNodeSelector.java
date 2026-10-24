package com.feifei.phonebridge;

import java.text.Normalizer;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/** Pure selector contract shared by cloud validation and Accessibility matching. */
final class XianyuNodeSelector {
    static final int MAX_VALUE_CHARS = 500;
    private static final Set<String> KINDS = immutableSet("resource_id", "text", "description");
    private static final Set<String> SELECTOR_FIELDS = immutableSet("kind", "value");

    private XianyuNodeSelector() {
    }

    static String validate(
            boolean selectorObject,
            Set<String> suppliedFields,
            Object rawKind,
            Object rawValue
    ) {
        Set<String> fields = suppliedFields == null
                ? Collections.emptySet() : suppliedFields;
        if (!selectorObject || !fields.equals(SELECTOR_FIELDS)) {
            return "invalid_node_selector";
        }
        if (!(rawKind instanceof String) || !KINDS.contains(normalize((String) rawKind))) {
            return "invalid_node_selector_kind";
        }
        if (!(rawValue instanceof String)) {
            return "invalid_node_selector_value";
        }
        String normalized = normalize((String) rawValue);
        if (normalized.isEmpty() || normalized.length() > MAX_VALUE_CHARS) {
            return "invalid_node_selector_value";
        }
        return null;
    }

    static String normalize(String value) {
        if (value == null) {
            return "";
        }
        return Normalizer.normalize(value, Normalizer.Form.NFKC).trim();
    }

    static boolean matches(
            String kind,
            String expected,
            String resourceId,
            CharSequence text,
            CharSequence description,
            boolean password
    ) {
        String normalizedKind = normalize(kind);
        String normalizedExpected = normalize(expected);
        if (normalizedExpected.isEmpty()) {
            return false;
        }
        switch (normalizedKind) {
            case "resource_id":
                return normalizedExpected.equals(normalize(resourceId));
            case "text":
                return !password && normalizedExpected.equals(normalize(
                        text == null ? "" : text.toString()));
            case "description":
                return !password && normalizedExpected.equals(normalize(
                        description == null ? "" : description.toString()));
            default:
                return false;
        }
    }

    static Set<String> selectorFields() {
        return SELECTOR_FIELDS;
    }

    private static Set<String> immutableSet(String... values) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(values)));
    }
}
