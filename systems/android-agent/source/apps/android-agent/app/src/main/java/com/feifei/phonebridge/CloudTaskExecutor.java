package com.feifei.phonebridge;

import android.content.Context;

import com.feifei.phonebridge.plugin.xianyu.XianyuSnapshotCapture;

import org.json.JSONObject;

final class CloudTaskExecutor {
    private final Context context;

    CloudTaskExecutor(Context context) {
        this.context = context.getApplicationContext();
    }

    Result execute(String taskType) {
        return execute(taskType, new JSONObject());
    }

    Result execute(String taskType, JSONObject parameters) {
        return execute(taskType, parameters, Long.MAX_VALUE);
    }

    Result execute(String taskType, JSONObject parameters, long remainingTaskBudgetMillis) {
        try {
            String requestedAction = parameters.optString("action", "").trim();
            boolean needsAccessibility = "observe".equals(taskType) || "xianyu_snapshot".equals(taskType)
                    || ("action".equals(taskType) && !"pause".equals(requestedAction) && !"resume".equals(requestedAction));
            if (needsAccessibility && !AccessibilityRecovery.connected()) {
                long started = android.os.SystemClock.elapsedRealtime();
                AccessibilityRecovery.await(context, Math.max(0L, remainingTaskBudgetMillis - 4000L));
                remainingTaskBudgetMillis = Math.max(0L, remainingTaskBudgetMillis -
                        (android.os.SystemClock.elapsedRealtime() - started));
                if (Thread.currentThread().isInterrupted()) return Result.failed("execution_interrupted");
            }
            switch (taskType) {
                case "health":
                    JSONObject health = BridgeState.status(context);
                    health.put("cloud_transport", CloudConfig.transportCode());
                    health.put("cloud_connected", ControlService.isCloudChannelFresh(context));
                    health.put("cloud_durability_compat_id",
                            BuildConfig.CLOUD_DURABILITY_COMPAT_ID);
                    return Result.succeeded(health);
                case "observe":
                    BridgeAccessibilityService observeService = BridgeAccessibilityService.getInstance();
                    if (observeService == null) {
                        return Result.failed("accessibility_not_connected");
                    }
                    return Result.succeeded(observeService.observe());
                case "xianyu_snapshot":
                    XianyuExecutionEnvironment.Snapshot snapshotEnvironment =
                            XianyuExecutionEnvironment.capture(context);
                    XianyuTaskAdmissionRules.Decision snapshotAdmission =
                            XianyuTaskAdmissionRules.snapshot(snapshotEnvironment.state);
                    if (snapshotAdmission.outcome != XianyuTaskAdmissionRules.Outcome.ALLOW) {
                        return Result.failed(snapshotAdmission.code);
                    }
                    BridgeAccessibilityService xianyuService = BridgeAccessibilityService.getInstance();
                    if (xianyuService == null) {
                        return Result.failed("accessibility_not_connected");
                    }
                    return Result.succeeded(XianyuSnapshotCapture.capture(
                            xianyuService::observe,
                            remainingTaskBudgetMillis));
                case "action":
                    String action = parameters.optString("action", "").trim();
                    boolean environmentIndependentAction =
                            "pause".equals(action) || "resume".equals(action);
                    ExecutionEnvironmentRules.State actionEnvironmentState =
                            environmentIndependentAction
                                    ? ExecutionEnvironmentRules.State.READY
                                    : XianyuExecutionEnvironment.capture(context).state;
                    XianyuTaskAdmissionRules.Decision actionAdmission =
                            XianyuTaskAdmissionRules.action(
                                    actionEnvironmentState,
                                    action,
                                    parameters.optString("package", "")
                            );
                    if (actionAdmission.outcome == XianyuTaskAdmissionRules.Outcome.REJECT) {
                        return Result.rejected(actionAdmission.code);
                    }
                    if (actionAdmission.outcome == XianyuTaskAdmissionRules.Outcome.FAIL) {
                        return Result.failed(actionAdmission.code);
                    }
                    JSONObject actionResult = new ActionDispatcher(context).dispatch(
                            parameters,
                            remainingTaskBudgetMillis
                    );
                    if (!actionResult.optBoolean("success", false)) {
                        return Result.failed(safeActionError(
                                actionResult.optString("message", "action_failed")));
                    }
                    return Result.succeeded(actionResult);
                default:
                    return Result.rejected("unknown_task_type");
            }
        } catch (Exception failure) {
            return Result.failed("local_executor_error");
        } catch (LinkageError failure) {
            return Result.failed("local_executor_linkage_error");
        }
    }

    private static String safeActionError(String value) {
        String normalized = value == null ? "" : value.trim();
        if (!normalized.matches("[a-z0-9_]{1,64}")) {
            return "action_failed";
        }
        return normalized;
    }

    static final class Result {
        final String status;
        final JSONObject result;
        final String errorCode;

        private Result(String status, JSONObject result, String errorCode) {
            this.status = status;
            this.result = result;
            this.errorCode = errorCode;
        }

        static Result succeeded(JSONObject result) {
            return new Result("succeeded", result, null);
        }

        static Result failed(String errorCode) {
            return new Result("failed", null, errorCode);
        }

        static Result rejected(String errorCode) {
            return new Result("rejected", null, errorCode);
        }
    }
}
