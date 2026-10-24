package com.feifei.phonebridge.plugin.xianyu;

import com.feifei.phonebridge.plugin.messaging.MessagingPlatformRegistry;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * Captures one atomic Xianyu snapshot and, only when the first traversal was truncated,
 * performs one bounded read-only retry after the page has had time to settle.
 */
public final class XianyuSnapshotCapture {
    private XianyuSnapshotCapture() {
    }

    public interface Observer {
        JSONObject observe() throws JSONException;
    }

    interface Sleeper {
        void sleep(long delayMillis) throws InterruptedException;
    }

    public static JSONObject capture(Observer observer, long remainingTaskBudgetMillis)
            throws JSONException, InterruptedException {
        return capture(observer, remainingTaskBudgetMillis, Thread::sleep);
    }

    static JSONObject capture(Observer observer, long remainingTaskBudgetMillis, Sleeper sleeper)
            throws JSONException, InterruptedException {
        JSONObject first = observer.observe();
        JSONObject selected = first;
        int attempts = 1;
        String retryOutcome = "not_needed";
        boolean firstIsXianyu = XianYuPage.PACKAGE.equals(first.optString("package", ""));
        if (XianyuSnapshotRetryPolicy.shouldRetry(
                firstIsXianyu,
                first.optBoolean("truncated", false),
                remainingTaskBudgetMillis)) {
            sleeper.sleep(XianyuSnapshotRetryPolicy.STABILIZATION_DELAY_MILLIS);
            JSONObject second = observer.observe();
            attempts = 2;
            String firstPage = XianYuPage.classify(first).name();
            String secondPage = XianYuPage.classify(second).name();
            boolean useSecond = XianyuSnapshotRetryPolicy.shouldUseSecond(
                    firstPage,
                    first.optInt("node_count", 0),
                    XianYuPage.PACKAGE.equals(second.optString("package", "")),
                    secondPage,
                    second.optInt("node_count", 0),
                    second.optBoolean("truncated", false));
            if (useSecond) {
                selected = second;
                retryOutcome = "second_snapshot_selected";
            } else {
                retryOutcome = "first_snapshot_retained_after_page_change_or_regression";
            }
        }
        JSONObject result = MessagingPlatformRegistry.snapshot("xianyu", selected);
        result.put("capture_attempts", attempts);
        result.put("stable_retry_outcome", retryOutcome);
        return result;
    }
}
