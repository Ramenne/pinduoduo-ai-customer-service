package com.feifei.phonebridge;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/** Pure, deterministic validation for parameters received from the cloud task channel. */
final class CloudTaskParametersContract {
    private static final String ACTION = "action";
    private static final String XIANYU_PACKAGE = "com.taobao.idlefish";
    private static final Set<String> LAUNCH_FIELDS = immutableSet("action", "package");
    private static final Set<String> CLICK_FIELDS = immutableSet("action", "package", "selector");
    private static final Set<String> SET_FIELDS = immutableSet("action", "package", "selector", "value");
    private static final Set<String> DESCRIPTION_FIELDS = immutableSet("action", "package", "value");
    private static final Set<String> IMAGE_FIELDS = immutableSet(
            "action", "package", "mime_type", "filename", "sha256", "data_base64");
    private static final Set<String> SCROLL_FIELDS = immutableSet("action", "package", "direction");
    private static final Set<String> GLOBAL_FIELDS = immutableSet("action");

    private CloudTaskParametersContract() {
    }

    static String validate(
            String taskType,
            Set<String> suppliedFields,
            String action,
            String packageName
    ) {
        return validate(
                taskType,
                suppliedFields,
                action,
                packageName,
                false,
                Collections.emptySet(),
                null,
                null
        );
    }

    static String validate(
            String taskType,
            Set<String> suppliedFields,
            Object rawAction,
            Object rawPackageName,
            boolean selectorObject,
            Set<String> selectorFields,
            Object selectorKind,
            Object selectorValue
    ) {
        return validate(
                taskType,
                suppliedFields,
                rawAction,
                rawPackageName,
                selectorObject,
                selectorFields,
                selectorKind,
                selectorValue,
                null
        );
    }

    static String validate(
            String taskType,
            Set<String> suppliedFields,
            Object rawAction,
            Object rawPackageName,
            boolean selectorObject,
            Set<String> selectorFields,
            Object selectorKind,
            Object selectorValue,
            Object rawDirection
    ) {
        return validate(taskType, suppliedFields, rawAction, rawPackageName, selectorObject,
                selectorFields, selectorKind, selectorValue, rawDirection,
                null, null, null, null, null);
    }

    // Keep all older pure-Java overloads source-compatible.
    static String validate(
            String taskType, Set<String> suppliedFields, Object rawAction, Object rawPackageName,
            boolean selectorObject, Set<String> selectorFields, Object selectorKind,
            Object selectorValue, Object rawDirection, Object rawValue, Object rawMimeType,
            Object rawFilename, Object rawSha256, Object rawDataBase64
    ) {
        Set<String> fields = suppliedFields == null
                ? Collections.emptySet() : suppliedFields;
        if (!ACTION.equals(taskType)) {
            return fields.isEmpty() ? null : "parameters_not_allowed";
        }
        String normalizedAction = rawAction instanceof String
                ? ((String) rawAction).trim() : "";
        if("set_description_text".equals(normalizedAction)){
            if(!fields.equals(DESCRIPTION_FIELDS))return "invalid_description_parameters";
            if(!XIANYU_PACKAGE.equals(rawPackageName))return "description_package_not_allowed";
            return BoundedXianyuActions.validateText(rawValue);
        }
        if ("set_node_text".equals(normalizedAction) ||
                "click_unique_node".equals(normalizedAction) || "tap_unique_node".equals(normalizedAction)) {
            if("class".equals(selectorKind) && (!"set_node_text".equals(normalizedAction) || !"android.widget.EditText".equals(selectorValue)))return "invalid_node_selector_kind";
            boolean setText = "set_node_text".equals(normalizedAction);
            if (!fields.equals(setText ? SET_FIELDS : CLICK_FIELDS)) {
                return "invalid_" + normalizedAction + "_parameters";
            }
            if (!XIANYU_PACKAGE.equals(rawPackageName)) {
                return "cloud_node_package_not_allowed";
            }
            String error = BoundedXianyuActions.validateSelector(
                    selectorObject, selectorFields, selectorKind, selectorValue);
            if (error != null) return error;
            return setText ? BoundedXianyuActions.validateText(rawValue) : null;
        }
        if ("import_image".equals(normalizedAction)) {
            if (!fields.equals(IMAGE_FIELDS)) return "invalid_import_image_parameters";
            if (!XIANYU_PACKAGE.equals(rawPackageName)) return "cloud_image_package_not_allowed";
            return ImageImportContract.validate(rawMimeType, rawFilename, rawSha256, rawDataBase64);
        }
        if ("tap_free_brighten".equals(normalizedAction)) {
            return fields.equals(LAUNCH_FIELDS) && XIANYU_PACKAGE.equals(rawPackageName) ? null : "invalid_free_brighten_parameters";
        }
        if ("launch_app".equals(normalizedAction)) {
            if (!fields.equals(LAUNCH_FIELDS)) {
                return "invalid_launch_app_parameters";
            }
            return XIANYU_PACKAGE.equals(rawPackageName)
                    ? null : "cloud_launch_package_not_allowed";
        }
        if ("click_node".equals(normalizedAction)) {
            if (!fields.equals(CLICK_FIELDS)) {
                return "invalid_click_node_parameters";
            }
            if (!XIANYU_PACKAGE.equals(rawPackageName)) {
                return "cloud_click_package_not_allowed";
            }
            return XianyuNodeSelector.validate(
                    selectorObject,
                    selectorFields,
                    selectorKind,
                    selectorValue
            );
        }
        if ("scroll_page".equals(normalizedAction)) {
            if (!fields.equals(SCROLL_FIELDS)) {
                return "invalid_scroll_page_parameters";
            }
            if (!XIANYU_PACKAGE.equals(rawPackageName)) {
                return "cloud_scroll_package_not_allowed";
            }
            if (!(rawDirection instanceof String)) {
                return "invalid_scroll_direction";
            }
            String direction = ((String) rawDirection).trim();
            return "up".equals(direction) || "down".equals(direction)
                    ? null : "invalid_scroll_direction";
        }
        if ("back".equals(normalizedAction) || "home".equals(normalizedAction)) {
            return fields.equals(GLOBAL_FIELDS)
                    ? null : "invalid_global_action_parameters";
        }
        return "unsupported_cloud_action";
    }

    private static Set<String> immutableSet(String... values) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(values)));
    }
}
