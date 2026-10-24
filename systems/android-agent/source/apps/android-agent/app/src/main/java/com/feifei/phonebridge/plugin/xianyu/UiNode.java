package com.feifei.phonebridge.plugin.xianyu;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * observe() UI 树中单个节点的轻量封装。
 * 字段与 BridgeAccessibilityService.toJson() 输出一一对应。
 * 不依赖 Android SDK，可在 PC 端单测（用实测 JSON 回放）。
 */
public final class UiNode {
    public final int index;
    public final int depth;
    public final String className;
    public final String resourceId;
    public final String text;
    public final String description;
    public final boolean clickable;
    public final boolean editable;
    public final boolean scrollable;
    public final boolean enabled;
    public final int[] bounds; // [left, top, right, bottom]

    private UiNode(JSONObject j) {
        this(
                j.optInt("index", -1),
                j.optInt("depth", 0),
                j.optString("class", ""),
                j.optString("resource_id", ""),
                j.optString("text", ""),
                j.optString("description", ""),
                j.optBoolean("clickable", false),
                j.optBoolean("editable", false),
                j.optBoolean("scrollable", false),
                j.optBoolean("enabled", true),
                parseBounds(j.optJSONArray("bounds"))
        );
    }

    private UiNode(int index, int depth, String className, String resourceId, String text,
                   String description, boolean clickable, boolean editable, boolean scrollable,
                   boolean enabled, int[] bounds) {
        this.index = index;
        this.depth = depth;
        this.className = className == null ? "" : className;
        this.resourceId = resourceId == null ? "" : resourceId;
        this.text = text == null ? "" : text;
        this.description = description == null ? "" : description;
        this.clickable = clickable;
        this.editable = editable;
        this.scrollable = scrollable;
        this.enabled = enabled;
        this.bounds = bounds == null || bounds.length != 4
                ? new int[]{0, 0, 0, 0}
                : bounds.clone();
    }

    private static int[] parseBounds(JSONArray b) {
        return (b != null && b.length() == 4)
                ? new int[]{b.optInt(0), b.optInt(1), b.optInt(2), b.optInt(3)}
                : new int[]{0, 0, 0, 0};
    }

    /** 供不依赖 Android 运行时的样本回放使用。 */
    static UiNode fromValues(int index, int depth, String className, String resourceId, String text,
                             String description, boolean clickable, boolean editable,
                             boolean scrollable, boolean enabled, int[] bounds) {
        return new UiNode(index, depth, className, resourceId, text, description, clickable,
                editable, scrollable, enabled, bounds);
    }

    /** 从 observe() 返回 JSON 解析出节点数组 */
    public static UiNode[] fromObserve(JSONObject observeJson) {
        JSONArray arr = observeJson == null ? null : observeJson.optJSONArray("nodes");
        if (arr == null) {
            return new UiNode[0];
        }
        UiNode[] out = new UiNode[arr.length()];
        for (int i = 0; i < arr.length(); i++) {
            out[i] = new UiNode(arr.optJSONObject(i) == null ? new JSONObject() : arr.optJSONObject(i));
        }
        return out;
    }

    /** 最佳可读文本：text 优先，空则 description */
    public String bestText() {
        if (text != null && !text.trim().isEmpty()) return text.trim();
        return description == null ? "" : description.trim();
    }

    /** 文本（含 desc）是否包含某子串 */
    public boolean textContains(String sub) {
        return sub != null && bestText().contains(sub);
    }

    /** resource_id 是否包含某片段（去掉包名前缀后比对也支持） */
    public boolean ridContains(String fragment) {
        return fragment != null && resourceId.contains(fragment);
    }

    public int centerX() {
        return bounds[0] + (bounds[2] - bounds[0]) / 2;
    }

    public int centerY() {
        return bounds[1] + (bounds[3] - bounds[1]) / 2;
    }

    public boolean hasValidBounds() {
        return bounds[2] > bounds[0] && bounds[3] > bounds[1];
    }

    public boolean containsCenter(UiNode other) {
        if (other == null || !hasValidBounds() || !other.hasValidBounds()) return false;
        int x = other.centerX();
        int y = other.centerY();
        return x >= bounds[0] && x <= bounds[2] && y >= bounds[1] && y <= bounds[3];
    }

    public long boundsArea() {
        if (!hasValidBounds()) return Long.MAX_VALUE;
        return (long) (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]);
    }

    @Override
    public String toString() {
        return "[" + index + "] d" + depth + " " + className + " \"" + bestText() + "\"";
    }
}
