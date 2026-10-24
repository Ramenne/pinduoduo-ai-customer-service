package com.feifei.phonebridge;

import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;

import org.json.JSONException;
import org.json.JSONObject;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;

final class ActionDispatcher {
    private final Context context;

    ActionDispatcher(Context context) {
        this.context = context.getApplicationContext();
    }

    JSONObject dispatch(JSONObject request) throws JSONException {
        return dispatch(request, ForegroundLaunchVerifier.MAX_CONFIRMATION_WAIT_MILLIS);
    }

    JSONObject dispatch(JSONObject request, long remainingTaskBudgetMillis) throws JSONException {
        String action = request.optString("action", "").trim();
        JSONObject result;
        if (action.isEmpty()) {
            return outcome(false, "missing_action");
        }
        if ("pause".equals(action)) {
            BridgeState.setPaused(context, true);
            result = outcome(true, "bridge_paused");
            AuditLog.append(context, action, true, result);
            ControlService.refreshNotification(context);
            return result;
        }
        if ("resume".equals(action)) {
            BridgeState.setPaused(context, false);
            result = outcome(true, "bridge_resumed");
            AuditLog.append(context, action, true, result);
            ControlService.refreshNotification(context);
            return result;
        }
        if (BridgeState.PAUSED.get()) {
            result = outcome(false, "bridge_paused");
            AuditLog.append(context, action, false, result);
            return result;
        }

        if ("tap_free_brighten".equals(action) || "set_node_text".equals(action) || "click_unique_node".equals(action) || "tap_unique_node".equals(action) ||
                "import_image".equals(action) || "set_description_text".equals(action)) {
            String error = validateBoundedAction(request);
            if (error != null) return outcome(false, error);
        }

        BridgeAccessibilityService service = BridgeAccessibilityService.getInstance();
        if ("launch_app".equals(action)) {
            result = launchApp(
                    request.optString("package", ""),
                    remainingTaskBudgetMillis
            );
        } else if ("import_image".equals(action)) {
            result = ImageImporter.importImage(context, request, remainingTaskBudgetMillis);
        } else if ("set_description_text".equals(action)) {
            result = OinInputService.input(request.getString("value"),remainingTaskBudgetMillis);
        } else if (service == null) {
            result = outcome(false, "accessibility_not_connected");
        } else {
            switch (action) {
                case "tap_free_brighten":
                    result=service.tapFreeBrighten();
                    break;
                case "set_node_text":
                case "click_unique_node":
                case "tap_unique_node":
                    JSONObject exactSelector = request.getJSONObject("selector");
                    result = service.uniqueNodeAction(request.getString("package"),
                            exactSelector.getString("kind"), exactSelector.getString("value"),
                            request.optString("value", ""), "set_node_text".equals(action),
                            remainingTaskBudgetMillis, "tap_unique_node".equals(action));
                    break;
                case "home":
                case "back":
                case "recents":
                case "notifications":
                case "quick_settings":
                    result = service.performGlobal(action);
                    break;
                case "click_text":
                    result = service.clickText(request.optString("text", ""));
                    break;
                case "click_node":
                    JSONObject selector = request.optJSONObject("selector");
                    if (selector == null) {
                        result = outcome(false, "invalid_node_selector");
                    } else {
                        result = service.clickNode(
                                request.optString("package", ""),
                                selector.optString("kind", ""),
                                selector.optString("value", "")
                        );
                    }
                    break;
                case "scroll_page":
                    result = service.scrollPage(
                            request.optString("package", ""),
                            request.optString("direction", "")
                    );
                    break;
                case "set_text":
                    result = service.setText(
                            request.optString("target_text", ""),
                            request.optString("resource_id", ""),
                            request.optString("value", "")
                    );
                    break;
                case "tap":
                    result = service.tap((float) request.optDouble("x", -1), (float) request.optDouble("y", -1));
                    break;
                case "swipe":
                    result = service.swipe(
                            (float) request.optDouble("from_x", -1),
                            (float) request.optDouble("from_y", -1),
                            (float) request.optDouble("to_x", -1),
                            (float) request.optDouble("to_y", -1),
                            request.optLong("duration_ms", 450L)
                    );
                    break;
                default:
                    result = outcome(false, "unsupported_action");
            }
        }
        AuditLog.append(context, action, result.optBoolean("success"), result);
        return result;
    }

    private static String validateBoundedAction(JSONObject request) {
        JSONObject selector = request.optJSONObject("selector");
        return CloudTaskParametersContract.validate("action", fields(request), request.opt("action"),
                request.opt("package"), selector != null, fields(selector),
                selector == null ? null : selector.opt("kind"),
                selector == null ? null : selector.opt("value"), request.opt("direction"),
                request.opt("value"), request.opt("mime_type"), request.opt("filename"),
                request.opt("sha256"), request.opt("data_base64"));
    }

    private static Set<String> fields(JSONObject object) {
        Set<String> result = new HashSet<>();
        if (object != null) {
            Iterator<String> keys = object.keys();
            while (keys.hasNext()) result.add(keys.next());
        }
        return result;
    }

    private JSONObject launchApp(String packageName, long remainingTaskBudgetMillis)
            throws JSONException {
        if (packageName == null || packageName.trim().isEmpty()) {
            return outcome(false, "missing_package");
        }
        BridgeAccessibilityService service = BridgeAccessibilityService.getInstance();
        if (service == null) {
            return outcome(false, "accessibility_not_connected");
        }
        if (packageName.equals(service.activePackage())) {
            return outcome(true, "app_already_foreground");
        }
        Intent intent = context.getPackageManager().getLaunchIntentForPackage(packageName);
        if (intent == null) {
            return outcome(false, "package_not_launchable");
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            context.startActivity(intent);
        } catch (RuntimeException rejectedByAndroid) {
            return outcome(false, "app_launch_rejected_by_android");
        }
        try {
            if (ForegroundLaunchVerifier.await(
                    packageName,
                    remainingTaskBudgetMillis,
                    service::activePackage,
                    SystemClock::elapsedRealtime,
                    Thread::sleep
            )) {
                return outcome(true, "app_launch_verified");
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return outcome(false, "app_launch_verification_interrupted");
        }
        return outcome(false, "app_launch_not_observed");
    }

    static JSONObject outcome(boolean success, String message) throws JSONException {
        JSONObject json = new JSONObject();
        json.put("success", success);
        json.put("message", message);
        return json;
    }
}
