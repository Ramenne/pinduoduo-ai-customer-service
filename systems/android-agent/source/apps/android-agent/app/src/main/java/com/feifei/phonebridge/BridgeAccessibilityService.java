package com.feifei.phonebridge;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

public final class BridgeAccessibilityService extends AccessibilityService {
    private static final int MAX_OBSERVE_NODES = 1000;
    // 动作查找维持底座原有上限，避免 observe 扩容意外放大点击/输入的最坏耗时。
    private static final int MAX_ACTION_SEARCH_NODES = 300;
    private static final int MAX_NODE_SELECTOR_SEARCH_NODES = 1000;
    private static final long NODE_SELECTOR_TIME_BUDGET_MS = 2400L;
    // observe 遍历时间预算（毫秒）：超时提前截断，防止超大页面卡死 HTTP 响应
    private static final long OBSERVE_TIME_BUDGET_MS = 3000L;
    // 完整 observe JSON 上限 768 KiB，为 outbox 900 KiB 单结果上限预留协议包络和错误余量。
    static final int OBSERVE_SERIALIZED_BUDGET_BYTES = 768 * 1024;
    private static final int OBSERVE_METADATA_RESERVE_BYTES = 8 * 1024;
    private static volatile BridgeAccessibilityService instance;
    static BridgeAccessibilityService getInstance() {
        return instance;
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        BridgeState.accessibilityConnected = true;
        ControlService.requestStart(this);
    }

    @Override
    public void onDestroy() {
        if (instance == this) {
            instance = null;
            BridgeState.accessibilityConnected = false;
        }
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) {
            return;
        }
        long now = System.currentTimeMillis();
        String eventPackage = event.getPackageName() == null
                ? "" : event.getPackageName().toString();
        String eventClass = event.getClassName() == null
                ? "" : event.getClassName().toString();
        String eventType = AccessibilityEvent.eventTypeToString(event.getEventType());
        if (eventType == null) {
            eventType = Integer.toString(event.getEventType());
        }
        BridgeState.lastAccessibilityEventAt = now;
        BridgeState.lastAccessibilityEventPackage = eventPackage;
        BridgeState.lastAccessibilityEventClass = eventClass;
        BridgeState.lastAccessibilityEventType = eventType;
        if (!eventPackage.isEmpty()) {
            BridgeState.foregroundPackage = eventPackage;
        }
        if (!eventClass.isEmpty()) {
            BridgeState.foregroundClass = eventClass;
        }
        ControlService.notifyAccessibilityActivity(this);
    }

    @Override
    public void onInterrupt() {
    }

    JSONObject tapFreeBrighten() throws JSONException {
        JSONObject snapshot=observe();
        if (!"com.taobao.idlefish".equals(snapshot.optString("package")) || snapshot.optBoolean("truncated")) return ActionDispatcher.outcome(false,"free_brighten_not_confirmed");
        JSONArray nodes=snapshot.getJSONArray("nodes");int tabs=0,drafts=0,offline=0,panelCount=0;JSONObject panel=null;
        for(int i=0;i<nodes.length();i++){
            JSONObject n=nodes.getJSONObject(i);if(!n.optBoolean("visible_to_user")||n.optBoolean("password"))continue;
            String label=n.optString("description");if(label.isEmpty())label=n.optString("text");
            if(label.trim().matches("(?:[0-9]+\\s*\\n\\s*)?在卖"))tabs++;
            if(label.equals("草稿"))drafts++;if(label.equals("已下架"))offline++;
            if(label.startsWith("今日数据\n")&&label.contains("\n一键擦亮\n")){panel=n;panelCount++;}
        }
        if(panelCount!=1||panel==null||!panel.optBoolean("enabled"))return ActionDispatcher.outcome(false,"free_brighten_not_confirmed");
        JSONArray box=panel.optJSONArray("bounds");if(box==null||box.length()!=4)return ActionDispatcher.outcome(false,"free_brighten_not_confirmed");
        int[] b=new int[]{box.getInt(0),box.getInt(1),box.getInt(2),box.getInt(3)};
        String label=panel.optString("description");if(label.isEmpty())label=panel.optString("text");
        android.util.DisplayMetrics metrics=getResources().getDisplayMetrics();
        float[] point=FreeBrightenTarget.locate(label,b,tabs==1&&drafts==1&&offline==1,metrics.widthPixels,metrics.heightPixels);
        if(point==null||BridgeState.PAUSED.get()||!"com.taobao.idlefish".equals(currentActivePackage()))return ActionDispatcher.outcome(false,"free_brighten_not_confirmed");
        return tap(point[0],point[1]);
    }

    JSONObject observe() throws JSONException {
        long startedAt = SystemClock.elapsedRealtime();
        long deadline = startedAt + OBSERVE_TIME_BUDGET_MS;
        JSONObject result = new JSONObject();
        result.put("package", BridgeState.foregroundPackage);
        result.put("class", BridgeState.foregroundClass);
        result.put("captured_at", System.currentTimeMillis());
        JSONArray nodes = new JSONArray();
        AccessibilityNodeInfo root = getRootInActiveWindow();
        boolean truncated = false;
        String truncationReason = "";
        if (root != null) {
            Deque<NodeDepth> queue = new ArrayDeque<>();
            queue.add(new NodeDepth(root, 0));
            Utf8JsonBudget nodeBudget = new Utf8JsonBudget(
                    OBSERVE_SERIALIZED_BUDGET_BYTES - OBSERVE_METADATA_RESERVE_BYTES
            );
            try {
                String rootPackage = root.getPackageName() == null ? "" : root.getPackageName().toString();
                String rootClass = root.getClassName() == null ? "" : root.getClassName().toString();
                result.put("package", rootPackage);
                result.put("class", rootClass);
                BridgeState.foregroundPackage = rootPackage;
                BridgeState.foregroundClass = rootClass;
                int index = 0;
                while (!queue.isEmpty()) {
                    if (index >= MAX_OBSERVE_NODES) {
                        truncated = true;
                        truncationReason = "node_limit";
                        break;
                    }
                    if (SystemClock.elapsedRealtime() >= deadline) {
                        truncated = true;
                        truncationReason = "time_budget";
                        break;
                    }
                    NodeDepth current = queue.removeFirst();
                    AccessibilityNodeInfo node = current.node;
                    try {
                        JSONObject nodeJson = toJson(node, index, current.depth);
                        if (!nodeBudget.tryConsumeJsonArrayEntry(
                                nodeJson.toString(),
                                nodes.length() > 0
                        )) {
                            truncated = true;
                            truncationReason = "byte_budget";
                            break;
                        }
                        nodes.put(nodeJson);
                        index++;
                        for (int childIndex = 0; childIndex < node.getChildCount(); childIndex++) {
                            AccessibilityNodeInfo child = null;
                            try {
                                if (SystemClock.elapsedRealtime() >= deadline) {
                                    truncated = true;
                                    truncationReason = "time_budget";
                                    break;
                                }
                                child = node.getChild(childIndex);
                                if (child == null) {
                                    continue;
                                }
                                if (index + queue.size() >= MAX_OBSERVE_NODES) {
                                    truncated = true;
                                    truncationReason = "node_limit";
                                    break;
                                }
                                queue.addLast(new NodeDepth(child, current.depth + 1));
                                child = null;
                            } finally {
                                recycleNode(child);
                            }
                        }
                    } finally {
                        recycleNode(node);
                    }
                }
            } finally {
                while (!queue.isEmpty()) {
                    recycleNode(queue.removeFirst().node);
                }
            }
        }
        result.put("truncated", truncated);
        result.put("truncation_reason", truncationReason);
        result.put("nodes", nodes);
        result.put("node_count", nodes.length());
        result.put("capture_duration_ms", SystemClock.elapsedRealtime() - startedAt);
        updateSerializedSize(result);
        while (Utf8JsonBudget.utf8Bytes(result.toString()) > OBSERVE_SERIALIZED_BUDGET_BYTES &&
                nodes.length() > 0) {
            nodes.remove(nodes.length() - 1);
            result.put("truncated", true);
            result.put("truncation_reason", "byte_budget");
            result.put("node_count", nodes.length());
            updateSerializedSize(result);
        }
        if (Utf8JsonBudget.utf8Bytes(result.toString()) > OBSERVE_SERIALIZED_BUDGET_BYTES) {
            throw new JSONException("observe_metadata_exceeds_byte_budget");
        }
        return result;
    }

    private static void updateSerializedSize(JSONObject result) throws JSONException {
        int reported = 0;
        for (int attempt = 0; attempt < 8; attempt++) {
            result.put("serialized_size_bytes", reported);
            int measured = Utf8JsonBudget.utf8Bytes(result.toString());
            if (measured == reported) {
                return;
            }
            reported = measured;
        }
        result.put("serialized_size_bytes", Utf8JsonBudget.utf8Bytes(result.toString()));
    }

    @SuppressWarnings("deprecation")
    private static void recycleNode(AccessibilityNodeInfo node) {
        if (node != null) {
            node.recycle();
        }
    }

    JSONObject clickText(String requested) throws JSONException {
        String query = requested == null ? "" : requested.trim();
        AccessibilityNodeInfo node = findNode(query, null, false);
        if (node == null) {
            return outcome(false, "text_not_found");
        }
        AccessibilityNodeInfo clickable = node;
        try {
            while (clickable != null && !clickable.isClickable()) {
                AccessibilityNodeInfo parent = clickable.getParent();
                recycleNode(clickable);
                clickable = parent;
            }
            boolean success = clickable != null &&
                    clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            return outcome(success, success ? "clicked" : "node_not_clickable");
        } finally {
            recycleNode(clickable);
        }
    }

    JSONObject clickNode(String expectedPackage, String kind, String requested) throws JSONException {
        JSONObject result = outcome(false, "node_not_found");
        result.put("selector_kind", XianyuNodeSelector.normalize(kind));
        String normalizedRequested = XianyuNodeSelector.normalize(requested);
        result.put("selector_value", normalizedRequested.length() > XianyuNodeSelector.MAX_VALUE_CHARS
                ? normalizedRequested.substring(0, XianyuNodeSelector.MAX_VALUE_CHARS)
                : normalizedRequested);
        result.put("matched_count", 0);
        result.put("visited_nodes", 0);
        result.put("search_truncated", false);
        result.put("search_scope", "current_visible_window");
        result.put("selection_policy", "first_visible_exact_match");

        String normalizedPackage = expectedPackage == null ? "" : expectedPackage.trim();
        if (!"com.taobao.idlefish".equals(normalizedPackage)) {
            result.put("message", "click_package_not_allowed");
            return result;
        }
        if (XianyuNodeSelector.validate(
                true,
                XianyuNodeSelector.selectorFields(),
                kind,
                requested
        ) != null) {
            result.put("message", "invalid_node_selector");
            return result;
        }

        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            result.put("message", "active_root_unavailable");
            return result;
        }
        String activePackage = root.getPackageName() == null
                ? "" : root.getPackageName().toString();
        result.put("active_package_before", activePackage);
        if (!normalizedPackage.equals(activePackage)) {
            recycleNode(root);
            result.put("message", "foreground_package_mismatch");
            return result;
        }

        Deque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(root);
        AccessibilityNodeInfo firstMatch = null;
        int matchedCount = 0;
        int visited = 0;
        boolean truncated = false;
        long startedAt = SystemClock.elapsedRealtime();
        while (!queue.isEmpty()) {
            if (visited >= MAX_NODE_SELECTOR_SEARCH_NODES ||
                    SystemClock.elapsedRealtime() - startedAt >= NODE_SELECTOR_TIME_BUDGET_MS) {
                truncated = true;
                break;
            }
            AccessibilityNodeInfo node = queue.removeFirst();
            boolean retained = false;
            boolean rootNode = visited == 0;
            visited++;
            try {
                boolean visibleToUser = node.isVisibleToUser();
                if (!VisibleNodeSearchPolicy.shouldTraverse(rootNode, visibleToUser)) {
                    continue;
                }
                if (VisibleNodeSearchPolicy.canMatch(visibleToUser) && XianyuNodeSelector.matches(
                        kind,
                        requested,
                        node.getViewIdResourceName(),
                        node.getText(),
                        node.getContentDescription(),
                        node.isPassword()
                )) {
                    matchedCount = 1;
                    firstMatch = node;
                    retained = true;
                    break;
                }
                for (int index = 0; index < node.getChildCount(); index++) {
                    AccessibilityNodeInfo child = node.getChild(index);
                    if (child != null) {
                        queue.addLast(child);
                    }
                }
            } finally {
                if (!retained) {
                    recycleNode(node);
                }
            }
        }
        recycleQueue(queue);
        result.put("matched_count", matchedCount);
        result.put("visited_nodes", visited);
        result.put("search_truncated", truncated);

        if (truncated) {
            recycleNode(firstMatch);
            result.put("message", "node_search_truncated");
            return result;
        }
        if (matchedCount == 0 || firstMatch == null) {
            result.put("message", "node_not_found");
            return result;
        }
        result.put("matched_node", summarizeActionNode(firstMatch));
        AccessibilityNodeInfo clickable = firstMatch;
        try {
            while (clickable != null && !clickable.isClickable()) {
                AccessibilityNodeInfo parent = clickable.getParent();
                recycleNode(clickable);
                clickable = parent;
            }
            if (clickable == null || !VisibleNodeSearchPolicy.canDispatch(
                    clickable.isVisibleToUser(),
                    clickable.isEnabled()
            )) {
                result.put("message", "node_not_interactable");
                return result;
            }
            boolean dispatched = clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            result.put("success", dispatched);
            result.put("message", dispatched ? "node_click_dispatched" : "node_click_rejected");
        } finally {
            recycleNode(clickable);
        }

        WindowIdentity after = readActiveWindowIdentity();
        result.put("active_package_after_immediate", after.packageName);
        return result;
    }

    JSONObject uniqueNodeAction(String expectedPackage, String kind, String selectorValue,
                                String value, boolean setText, long remainingBudgetMillis)
            throws JSONException {
        return uniqueNodeAction(expectedPackage, kind, selectorValue, value, setText, remainingBudgetMillis, false);
    }

    JSONObject uniqueNodeAction(String expectedPackage, String kind, String selectorValue,
                                String value, boolean setText, long remainingBudgetMillis, boolean tap)
            throws JSONException {
        long inputActionStarted=SystemClock.elapsedRealtime();
        JSONObject result = outcome(false, "node_not_found");
        result.put("verified", false);
        result.put("search_truncated", false);
        // Never include selector/body/description or a node summary in this result/audit.
        if (!BoundedXianyuActions.PACKAGE.equals(expectedPackage)) {
            result.put("message", "node_package_not_allowed");
            return result;
        }
        if (BoundedXianyuActions.validateSelector(true, XianyuNodeSelector.selectorFields(),
                kind, selectorValue) != null ||
                (setText && BoundedXianyuActions.validateText(value) != null)) {
            result.put("message", "invalid_node_action_parameters");
            return result;
        }
        long budget = Math.min(NODE_SELECTOR_TIME_BUDGET_MS, Math.max(0, remainingBudgetMillis));
        long deadline = SystemClock.elapsedRealtime() + budget;
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            result.put("message", "active_root_unavailable");
            return result;
        }
        final int windowId = root.getWindowId();
        final Rect surface = new Rect();
        root.getBoundsInScreen(surface);
        if (!expectedPackage.contentEquals(root.getPackageName() == null ? "" : root.getPackageName())) {
            recycleNode(root);
            result.put("message", "foreground_package_mismatch");
            return result;
        }
        UniqueNodeSearch.Result<AccessibilityNodeInfo> search;
        try {
            search = UniqueNodeSearch.find(root, new UniqueNodeSearch.Access<AccessibilityNodeInfo>() {
                public boolean matches(AccessibilityNodeInfo node) {
                    return node.isVisibleToUser() && expectedPackage.contentEquals(
                            node.getPackageName() == null ? "" : node.getPackageName()) &&
                            nodeMatches(kind,selectorValue,node);
                }
                public int childCount(AccessibilityNodeInfo node) { return node.getChildCount(); }
                public AccessibilityNodeInfo child(AccessibilityNodeInfo node, int index) {
                    return node.getChild(index);
                }
                public void release(AccessibilityNodeInfo node) { recycleNode(node); }
            }, MAX_NODE_SELECTOR_SEARCH_NODES, deadline, SystemClock::elapsedRealtime);
        } catch (RuntimeException unavailable) {
            result.put("message", "node_search_unavailable");
            return result;
        }
        result.put("matched_count", search.matches);
        result.put("visited_nodes", search.visited);
        result.put("search_truncated", search.truncated);
        if (search.truncated || search.node == null) {
            result.put("message", search.truncated ? "node_search_truncated" :
                    search.matches > 1 ? "node_not_unique" : "node_not_found");
            return result;
        }
        AccessibilityNodeInfo node = search.node;
        try {
            if (SystemClock.elapsedRealtime() >= deadline || Thread.currentThread().isInterrupted()) {
                result.put("message", "node_action_budget_exhausted");
                return result;
            }
            if (!node.refresh() || !sameActiveWindow(expectedPackage, windowId) ||
                    node.getWindowId() != windowId || !expectedPackage.contentEquals(
                    node.getPackageName() == null ? "" : node.getPackageName()) ||
                    !nodeMatches(kind,selectorValue,node)) {
                result.put("message", "node_or_foreground_changed");
                return result;
            }
            if (!node.isVisibleToUser() || !node.isEnabled() || node.isPassword() ||
                    (setText ? !node.isEditable() : !tap && !node.isClickable())) {
                result.put("message", "node_not_interactable");
                return result;
            }
            if (SystemClock.elapsedRealtime() >= deadline || Thread.currentThread().isInterrupted() ||
                    !sameActiveWindow(expectedPackage, windowId)) {
                result.put("message", "node_or_foreground_changed");
                return result;
            }
            // Click only the exact matched node; never walk to a different clickable ancestor.
            if (!setText) {
                if (tap && !node.isClickable()) {
                    Rect bounds = new Rect();
                    node.getBoundsInScreen(bounds);
                    if (!NodeTapPolicy.safe(node.isVisibleToUser(), node.isEnabled(), node.isPassword(),
                            bounds.left, bounds.top, bounds.right, bounds.bottom,
                            surface.left, surface.top, surface.right, surface.bottom) ||
                            deadline - SystemClock.elapsedRealtime() < 150 ||
                            !sameActiveWindow(expectedPackage, windowId)) {
                        result.put("message", "node_tap_surface_or_budget_invalid");
                        return result;
                    }
                    Path path = new Path();
                    path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
                    JSONObject gesture = dispatch(path, 0L, 80L, "node_tap",
                            Math.max(1L, deadline - SystemClock.elapsedRealtime()));
                    result.put("success", gesture.optBoolean("success"));
                    result.put("message", gesture.optString("message"));
                    return result;
                }
                boolean dispatched = node.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                result.put("success", dispatched);
                result.put("message", dispatched ? "node_click_dispatched" : "node_click_rejected");
                return result; // verified remains false: dispatch is not business/page success.
            }
            if(OinInputService.optedIn(this)&&"class".equals(kind)&&"android.widget.EditText".equals(selectorValue)){
                // Existing command contract is unchanged; routing is a local user preference.
                // Never fall back after an IME attempt: the effect may already have happened.
                if(!node.isFocused())node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
                return OinInputService.inputSearch(this,value,windowId,Math.max(0,remainingBudgetMillis-(SystemClock.elapsedRealtime()-inputActionStarted)));
            }
            Bundle arguments = new Bundle();
            arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
            if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)) {
                result.put("message", "node_set_text_rejected");
                return result;
            }
            // Refresh the same node, not a text-selector that changes after setting it.
            while (SystemClock.elapsedRealtime() < deadline && !Thread.currentThread().isInterrupted()) {
                if (!sameActiveWindow(expectedPackage, windowId) || !node.refresh() ||
                        node.getWindowId() != windowId || !expectedPackage.contentEquals(
                        node.getPackageName() == null ? "" : node.getPackageName()) ||
                        !BoundedXianyuActions.maySet(node.isVisibleToUser(), node.isEnabled(),
                                node.isEditable(), node.isPassword())) break;
                CharSequence actual = node.getText();
                if (actual != null && value.contentEquals(actual)) {
                    result.put("success", true);
                    result.put("verified", true);
                    result.put("message", "node_text_readback_verified");
                    return result;
                }
                try {
                    Thread.sleep(Math.min(50L, Math.max(1L, deadline - SystemClock.elapsedRealtime())));
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
            result.put("message", "node_text_readback_not_verified");
            return result;
        } catch (RuntimeException unavailable) {
            result.put("message", "node_action_unavailable");
            return result;
        } finally {
            recycleNode(node);
        }
    }

    boolean focusedSearchEditor(int windowId){
        AccessibilityNodeInfo root=getRootInActiveWindow(),focused=null;
        try{
            if(root==null||root.getWindowId()!=windowId||!"com.taobao.idlefish".contentEquals(root.getPackageName()==null?"":root.getPackageName()))return false;
            focused=root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if(focused==null||!focused.isFocused()||!focused.isVisibleToUser()||!focused.isEnabled()||!focused.isEditable()||focused.isPassword()||
                    focused.getWindowId()!=windowId||!"android.widget.EditText".contentEquals(focused.getClassName()==null?"":focused.getClassName()))return false;
            AccessibilityNodeInfo searchRoot=root;root=null;
            UniqueNodeSearch.Result<AccessibilityNodeInfo> search=UniqueNodeSearch.find(searchRoot,new UniqueNodeSearch.Access<AccessibilityNodeInfo>(){
                public boolean matches(AccessibilityNodeInfo node){return node.isVisibleToUser()&&node.isEnabled()&&
                        ("搜索".contentEquals(node.getText()==null?"":node.getText())||"搜索".contentEquals(node.getContentDescription()==null?"":node.getContentDescription()));}
                public int childCount(AccessibilityNodeInfo node){return node.getChildCount();}
                public AccessibilityNodeInfo child(AccessibilityNodeInfo node,int index){return node.getChild(index);}
                public void release(AccessibilityNodeInfo node){recycleNode(node);}
            },MAX_NODE_SELECTOR_SEARCH_NODES,SystemClock.elapsedRealtime()+300,SystemClock::elapsedRealtime);
            // UniqueNodeSearch consumes/releases its root, including on failure.
            root=null;
            boolean ready=!search.truncated&&search.node!=null&&search.matches==1;
            recycleNode(search.node);return ready;
        }catch(RuntimeException unavailable){return false;}
        finally{recycleNode(focused);recycleNode(root);}
    }

    boolean sameActiveWindow(String expectedPackage, int windowId) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        try {
            return root != null && root.getWindowId() == windowId && expectedPackage.contentEquals(
                    root.getPackageName() == null ? "" : root.getPackageName());
        } finally {
            recycleNode(root);
        }
    }

    int descriptionInputWindow(boolean requireEmpty) {
        AccessibilityNodeInfo root=getRootInActiveWindow();
        int window;
        try{
            if(root==null||!DescriptionInputPolicy.PACKAGE.contentEquals(root.getPackageName()==null?"":root.getPackageName()))return -1;
            window=root.getWindowId();
        }finally{recycleNode(root);}
        try{
            JSONObject snapshot=observe();
            if(snapshot.optBoolean("truncated")||!DescriptionInputPolicy.PACKAGE.equals(snapshot.optString("package")))return -1;
            String[] anchors=requireEmpty?new String[]{"发布, 发布","完成",DescriptionInputPolicy.PLACEHOLDER}:new String[]{"发布, 发布","完成"};
            JSONArray nodes=snapshot.getJSONArray("nodes");
            for(String anchor:anchors){
                int count=0;
                for(int i=0;i<nodes.length();i++){JSONObject node=nodes.getJSONObject(i);
                    if(node.optBoolean("visible_to_user")&&node.optBoolean("enabled")&&!node.optBoolean("password")&&anchor.equals(node.optString("description")))count++;
                }
                if(count!=1)return -1;
            }
            return sameActiveWindow(DescriptionInputPolicy.PACKAGE,window)?window:-1;
        }catch(JSONException|RuntimeException unavailable){return -1;}
    }

    boolean clickExactText(String requested) {
        AccessibilityNodeInfo node = findExactNode(requested, false);
        if (node == null) {
            return false;
        }
        AccessibilityNodeInfo clickable = node;
        try {
            while (clickable != null && !clickable.isClickable()) {
                AccessibilityNodeInfo parent = clickable.getParent();
                recycleNode(clickable);
                clickable = parent;
            }
            return clickable != null &&
                    clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        } finally {
            recycleNode(clickable);
        }
    }

    boolean clickFirstExact(String... requestedValues) {
        if (requestedValues == null) {
            return false;
        }
        for (String value : requestedValues) {
            if (clickExactText(value)) {
                return true;
            }
        }
        return false;
    }

    boolean hasExactText(String requested) {
        AccessibilityNodeInfo node = findExactNode(requested, false);
        try {
            return node != null;
        } finally {
            recycleNode(node);
        }
    }

    boolean setFirstEditableText(String value) {
        AccessibilityNodeInfo node = findExactNode("", true);
        if (node == null) {
            return false;
        }
        try {
            Bundle arguments = new Bundle();
            arguments.putCharSequence(
                    AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                    value == null ? "" : value
            );
            return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
        } finally {
            recycleNode(node);
        }
    }

    WindowIdentity readActiveWindowIdentity() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        String packageName = "";
        String className = "";
        try {
            if (root != null) {
                packageName = root.getPackageName() == null
                        ? "" : root.getPackageName().toString();
                className = root.getClassName() == null
                        ? "" : root.getClassName().toString();
            }
        } finally {
            recycleNode(root);
        }
        long now = System.currentTimeMillis();
        BridgeState.lastActiveWindowPackage = packageName;
        BridgeState.lastActiveWindowClass = className;
        BridgeState.lastActiveWindowCheckedAt = now;
        if (!packageName.isEmpty()) {
            BridgeState.foregroundPackage = packageName;
        }
        if (!className.isEmpty()) {
            BridgeState.foregroundClass = className;
        }
        return new WindowIdentity(packageName, className, now);
    }

    String activePackage() {
        WindowIdentity identity = readActiveWindowIdentity();
        return identity.packageName.isEmpty()
                ? BridgeState.foregroundPackage : identity.packageName;
    }

    /** Current root only; unlike activePackage(), never falls back to stale event state. */
    String currentActivePackage() {
        return readActiveWindowIdentity().packageName;
    }

    static final class WindowIdentity {
        final String packageName;
        final String className;
        final long checkedAt;

        WindowIdentity(String packageName, String className, long checkedAt) {
            this.packageName = packageName == null ? "" : packageName;
            this.className = className == null ? "" : className;
            this.checkedAt = checkedAt;
        }
    }

    JSONObject setText(String target, String resourceId, String value) throws JSONException {
        AccessibilityNodeInfo node = findNode(target, resourceId, true);
        if (node == null) {
            return outcome(false, "editable_node_not_found");
        }
        try {
            Bundle arguments = new Bundle();
            arguments.putCharSequence(
                    AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                    value == null ? "" : value
            );
            boolean success = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
            return outcome(success, success ? "text_set" : "set_text_rejected");
        } finally {
            recycleNode(node);
        }
    }

    JSONObject performGlobal(String action) throws JSONException {
        int globalAction;
        switch (action) {
            case "home": globalAction = GLOBAL_ACTION_HOME; break;
            case "back": globalAction = GLOBAL_ACTION_BACK; break;
            case "recents": globalAction = GLOBAL_ACTION_RECENTS; break;
            case "notifications": globalAction = GLOBAL_ACTION_NOTIFICATIONS; break;
            case "quick_settings": globalAction = GLOBAL_ACTION_QUICK_SETTINGS; break;
            default: return outcome(false, "unsupported_global_action");
        }
        boolean success = performGlobalAction(globalAction);
        return outcome(success, success ? "global_action_dispatched" : "global_action_rejected");
    }

    JSONObject tap(float x, float y) throws JSONException {
        if (!Float.isFinite(x) || !Float.isFinite(y) || x < 0F || y < 0F) {
            return outcome(false, "invalid_tap_coordinates");
        }
        Path path = new Path();
        path.moveTo(x, y);
        return dispatch(path, 1L, 80L, "tap");
    }

    JSONObject swipe(float fromX, float fromY, float toX, float toY, long durationMs) throws JSONException {
        if (!Float.isFinite(fromX) || !Float.isFinite(fromY) ||
                !Float.isFinite(toX) || !Float.isFinite(toY) ||
                fromX < 0F || fromY < 0F || toX < 0F || toY < 0F) {
            return outcome(false, "invalid_swipe_coordinates");
        }
        Path path = new Path();
        path.moveTo(fromX, fromY);
        path.lineTo(toX, toY);
        return dispatch(path, 0L, Math.max(100L, Math.min(durationMs, 3000L)), "swipe");
    }

    JSONObject scrollPage(String expectedPackage, String direction) throws JSONException {
        String normalizedPackage = expectedPackage == null ? "" : expectedPackage.trim();
        String normalizedDirection = direction == null ? "" : direction.trim();
        if (!"com.taobao.idlefish".equals(normalizedPackage)) {
            return outcome(false, "scroll_package_not_allowed");
        }
        if (!"up".equals(normalizedDirection) && !"down".equals(normalizedDirection)) {
            return outcome(false, "invalid_scroll_direction");
        }

        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            return outcome(false, "active_root_unavailable");
        }
        try {
            String activePackage = root.getPackageName() == null
                    ? "" : root.getPackageName().toString();
            if (!normalizedPackage.equals(activePackage)) {
                return outcome(false, "foreground_package_mismatch");
            }
            Rect bounds = new Rect();
            root.getBoundsInScreen(bounds);
            if (bounds.width() < 200 || bounds.height() < 400) {
                return outcome(false, "scroll_surface_unavailable");
            }
            float x = bounds.exactCenterX();
            float upper = bounds.top + bounds.height() * 0.28F;
            float lower = bounds.top + bounds.height() * 0.72F;
            Path path = new Path();
            if ("up".equals(normalizedDirection)) {
                path.moveTo(x, upper);
                path.lineTo(x, lower);
            } else {
                // Scroll toward later items: finger moves upward, content moves up.
                path.moveTo(x, lower);
                path.lineTo(x, upper);
            }
            JSONObject result = dispatch(path, 0L, 450L, "scroll_page");
            result.put("direction", normalizedDirection);
            result.put("active_package", activePackage);
            return result;
        } finally {
            recycleNode(root);
        }
    }

    private JSONObject dispatch(Path path, long start, long duration, String label) throws JSONException {
        return dispatch(path, start, duration, label, 4000L);
    }

    private JSONObject dispatch(Path path, long start, long duration, String label, long waitMillis) throws JSONException {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicBoolean completed = new AtomicBoolean(false);
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, start, duration))
                .build();
        boolean accepted = dispatchGesture(gesture, new GestureResultCallback() {
            @Override
            public void onCompleted(GestureDescription gestureDescription) {
                completed.set(true);
                latch.countDown();
            }

            @Override
            public void onCancelled(GestureDescription gestureDescription) {
                latch.countDown();
            }
        }, null);
        if (!accepted) {
            return outcome(false, label + "_rejected");
        }
        try {
            latch.await(waitMillis, TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
        return outcome(completed.get(), completed.get() ? label + "_completed" : label + "_cancelled");
    }

    private AccessibilityNodeInfo findNode(String target, String resourceId, boolean editableOnly) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            return null;
        }
        AccessibilityNodeInfo containsMatch = null;
        Deque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(root);
        int visited = 0;
        while (!queue.isEmpty() && visited < MAX_ACTION_SEARCH_NODES) {
            AccessibilityNodeInfo node = queue.removeFirst();
            boolean retained = false;
            visited++;
            try {
                if ((!editableOnly || node.isEditable()) && matchesResource(node, resourceId)) {
                    String text = visibleText(node);
                    if ((target == null || target.isEmpty()) &&
                            (resourceId == null || resourceId.isEmpty())) {
                        if (node.isFocused()) {
                            recycleQueue(queue);
                            recycleNode(containsMatch);
                            retained = true;
                            return node;
                        }
                        if (containsMatch == null) {
                            containsMatch = node;
                            retained = true;
                        }
                    } else if (target == null || target.isEmpty() || text.equals(target)) {
                        recycleQueue(queue);
                        recycleNode(containsMatch);
                        retained = true;
                        return node;
                    } else if (text.contains(target) && containsMatch == null) {
                        containsMatch = node;
                        retained = true;
                    }
                }
                for (int index = 0; index < node.getChildCount(); index++) {
                    AccessibilityNodeInfo child = node.getChild(index);
                    if (child != null) {
                        queue.addLast(child);
                    }
                }
            } finally {
                if (!retained) {
                    recycleNode(node);
                }
            }
        }
        recycleQueue(queue);
        return containsMatch;
    }

    private AccessibilityNodeInfo findExactNode(String requested, boolean editableOnly) {
        String query = requested == null ? "" : requested.trim();
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            return null;
        }
        Deque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(root);
        int visited = 0;
        while (!queue.isEmpty() && visited < MAX_ACTION_SEARCH_NODES) {
            AccessibilityNodeInfo node = queue.removeFirst();
            boolean retained = false;
            visited++;
            try {
                if ((!editableOnly || node.isEditable()) &&
                        (query.isEmpty()
                                ? node.isEditable()
                                : visibleText(node).trim().equals(query))) {
                    recycleQueue(queue);
                    retained = true;
                    return node;
                }
                for (int index = 0; index < node.getChildCount(); index++) {
                    AccessibilityNodeInfo child = node.getChild(index);
                    if (child != null) {
                        queue.addLast(child);
                    }
                }
            } finally {
                if (!retained) {
                    recycleNode(node);
                }
            }
        }
        recycleQueue(queue);
        return null;
    }

    private static void recycleQueue(Deque<AccessibilityNodeInfo> queue) {
        while (queue != null && !queue.isEmpty()) {
            recycleNode(queue.removeFirst());
        }
    }

    private static boolean matchesResource(AccessibilityNodeInfo node, String resourceId) {
        return resourceId == null || resourceId.isEmpty() || resourceId.equals(node.getViewIdResourceName());
    }

    private static String visibleText(AccessibilityNodeInfo node) {
        if (node.isPassword()) {
            return "";
        }
        CharSequence text = node.getText();
        if (text != null && text.length() > 0) {
            return text.toString();
        }
        CharSequence description = node.getContentDescription();
        return description == null ? "" : description.toString();
    }

    private static JSONObject toJson(AccessibilityNodeInfo node, int index, int depth) throws JSONException {
        JSONObject json = new JSONObject();
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        json.put("index", index);
        json.put("depth", depth);
        json.put("class", node.getClassName() == null ? "" : node.getClassName().toString());
        json.put("resource_id", node.getViewIdResourceName() == null ? "" : node.getViewIdResourceName());
        json.put("text", node.isPassword() ? "[REDACTED]" : safe(node.getText()));
        json.put("description", node.isPassword() ? "[REDACTED]" : safe(node.getContentDescription()));
        json.put("bounds", new JSONArray(new int[]{bounds.left, bounds.top, bounds.right, bounds.bottom}));
        json.put("clickable", node.isClickable());
        json.put("checkable", node.isCheckable());
        json.put("checked", node.isChecked());
        json.put("editable", node.isEditable());
        json.put("supports_set_text", node.getActionList().contains(
                AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_TEXT));
        json.put("visible_to_user", node.isVisibleToUser());
        json.put("enabled", node.isEnabled());
        json.put("focused", node.isFocused());
        json.put("scrollable", node.isScrollable());
        json.put("selected", node.isSelected());
        json.put("password", node.isPassword());
        if("收藏按钮".contentEquals(node.getContentDescription()==null?"":node.getContentDescription())) {
            int count=favoriteCount(node);
            if(count>=0)json.put("favorite_count",count);
        }
        return json;
    }

    private static boolean nodeMatches(String kind,String value,AccessibilityNodeInfo node) {
        if("class".equals(kind))return "android.widget.EditText".equals(value) &&
                value.contentEquals(node.getClassName()==null?"":node.getClassName()) &&
                node.isEditable() && !node.isPassword();
        return BoundedXianyuActions.matches(kind,value,node.getViewIdResourceName(),node.getText(),node.getContentDescription(),node.isPassword());
    }

    // Read the counter from this button's own children, even when its text has empty bounds.
    private static int favoriteCount(AccessibilityNodeInfo button) {
        Deque<AccessibilityNodeInfo> queue=new ArrayDeque<>();
        int result=-1,visited=0;
        try {
            for(int i=0;i<button.getChildCount()&&i<6;i++){AccessibilityNodeInfo child=button.getChild(i);if(child!=null)queue.add(child);}
            while(!queue.isEmpty()&&visited++<12) {
                AccessibilityNodeInfo child=queue.removeFirst();
                try {
                    String text=child.getText()==null?"":child.getText().toString();
                    if(text.matches("[0-9]{1,8}")) {
                        if(result!=-1)return -1;
                        result=Integer.parseInt(text);
                    }
                    for(int i=0;i<child.getChildCount()&&i<6&&queue.size()<12;i++){AccessibilityNodeInfo next=child.getChild(i);if(next!=null)queue.add(next);}
                }finally{child.recycle();}
            }
            return queue.isEmpty()?result:-1;
        } finally {while(!queue.isEmpty())queue.removeFirst().recycle();}
    }

    private static JSONObject summarizeActionNode(AccessibilityNodeInfo node) throws JSONException {
        JSONObject json = new JSONObject();
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        json.put("class", node.getClassName() == null ? "" : node.getClassName().toString());
        json.put("resource_id", node.getViewIdResourceName() == null ? "" : node.getViewIdResourceName());
        json.put("text", node.isPassword() ? "[REDACTED]" : safe(node.getText()));
        json.put("description", node.isPassword() ? "[REDACTED]" : safe(node.getContentDescription()));
        json.put("bounds", new JSONArray(new int[]{bounds.left, bounds.top, bounds.right, bounds.bottom}));
        json.put("clickable", node.isClickable());
        json.put("enabled", node.isEnabled());
        return json;
    }

    private static String safe(CharSequence text) {
        if (text == null) {
            return "";
        }
        String value = text.toString();
        if (value.matches("\\s*\\d{6,}\\s*") || value.matches("\\s*\\d{17}[0-9Xx]\\s*")) {
            return "[REDACTED_IDENTIFIER]";
        }
        if (value.matches("(?is).*(验证码|校验码|动态码|verification\\s*code|one[- ]time|otp).*")) {
            value = value.replaceAll("(?<!\\d)\\d{4,8}(?!\\d)", "[REDACTED_CODE]");
        }
        return value.length() > 500 ? value.substring(0, 500) : value;
    }

    private static JSONObject outcome(boolean success, String message) throws JSONException {
        JSONObject json = new JSONObject();
        json.put("success", success);
        json.put("message", message);
        return json;
    }

    private static final class NodeDepth {
        final AccessibilityNodeInfo node;
        final int depth;

        NodeDepth(AccessibilityNodeInfo node, int depth) {
            this.node = node;
            this.depth = depth;
        }
    }
}
