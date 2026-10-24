package com.feifei.phonebridge.plugin.xianyu;

import com.feifei.phonebridge.plugin.messaging.MessagingPlatformAdapter;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 闲鱼插件统一入口：对外暴露页面、商品、消息、快照和自检静态方法，
 * 供 HttpBridgeServer 的 /xianyu/* 端点直接调用。
 *
 * 设计原则：
 *   - 输入一律是 observe() 的原始 UI 树 JSON（底座感知层产物）；
 *   - 本包只做"识别与结构化"，不公开点击、输入、网络或 Shell 能力；
 *   - 底座本地诊断动作不属于本插件，且不经 Board 0 云端任务通道；
 *   - 仅启用有真机样本依据的 LOGIN/HOME/MESSAGES/CHAT/首页商品规则；
 *   - 未覆盖页面返回 OTHER/空数组，并携带 verified/scope，不输出猜测数据。
 */
public final class XianYuPlugin implements MessagingPlatformAdapter {

    public static final XianYuPlugin INSTANCE = new XianYuPlugin();
    private static final XianyuHistoryState HISTORY_STATE = new XianyuHistoryState();

    private XianYuPlugin() {
    }

    public static final String VERSION = "0.4.0-dev";

    @Override
    public String platformId() {
        return "xianyu";
    }

    @Override
    public String adapterId() {
        return "xianyu.android.accessibility";
    }

    @Override
    public String adapterVersion() {
        return VERSION;
    }

    @Override
    public String runtimeKind() {
        return "android_accessibility";
    }

    @Override
    public String targetAppId() {
        return XianYuPage.PACKAGE;
    }

    @Override
    public JSONObject buildSnapshot(JSONObject observation) throws org.json.JSONException {
        return snapshot(observation);
    }

    @Override
    public JSONObject describe() throws org.json.JSONException {
        return info();
    }

    /** 判定当前闲鱼页面状态 */
    public static JSONObject page(JSONObject observeJson) throws org.json.JSONException {
        return XianYuPage.classifyResult(observeJson);
    }

    /** 提取商品卡片（首页推荐流已实测，详情页待验证） */
    public static JSONObject items(JSONObject observeJson) throws org.json.JSONException {
        return ItemCardExtractor.extractResult(observeJson);
    }

    /** 提取消息会话列表或当前聊天窗口中的可见记录。 */
    public static JSONObject messages(JSONObject observeJson) throws org.json.JSONException {
        return MessageExtractor.extractResult(observeJson);
    }

    /**
     * Evaluate post-navigation identity and an optional outgoing exact-text read-back.
     * This helper is semantic only: it does not click, focus, input, or send.
     */
    public static JSONObject verifyNavigationAndReadback(
            JSONObject targetConversation,
            JSONObject snapshotOrMessagePayload,
            String expectedOutgoingText
    ) throws org.json.JSONException {
        return XianyuNavigationSemantics.evaluate(
                targetConversation,
                snapshotOrMessagePayload,
                expectedOutgoingText
        );
    }

    /** 单次 observe 的原子快照，避免分别调用 page/items/messages 得到不同 UI 时刻。 */
    public static JSONObject snapshot(JSONObject observeJson) throws org.json.JSONException {
        JSONObject out = new JSONObject();
        out.put("success", true);
        out.put("captured_at", observeJson == null ? 0L : observeJson.optLong("captured_at", 0L));
        out.put("node_count", observeJson == null ? 0 : observeJson.optInt("node_count", 0));
        out.put("truncated", observeJson != null && observeJson.optBoolean("truncated", false));
        out.put("truncation_reason", observeJson == null ? "" : observeJson.optString("truncation_reason", ""));
        out.put("page", page(observeJson));
        out.put("items", items(observeJson));
        out.put("messages", messages(observeJson));
        return HISTORY_STATE.enrich(out);
    }

    /** 插件自检信息 */
    public static JSONObject info() throws org.json.JSONException {
        JSONObject out = new JSONObject();
        out.put("plugin", "xianyu");
        out.put("version", VERSION);
        out.put("target_package", XianYuPage.PACKAGE);
        JSONArray verifiedPages = new JSONArray();
        verifiedPages.put("LOGIN");
        verifiedPages.put("HOME");
        verifiedPages.put("MESSAGES");
        verifiedPages.put("CHAT");
        out.put("verified_page_rules", verifiedPages);
        JSONArray pendingPages = new JSONArray();
        pendingPages.put("LAUNCH");
        pendingPages.put("ITEM_DETAIL");
        out.put("pending_page_rules", pendingPages);
        out.put("item_rules_verified_scope", "offline_replay:xianyu_01,xianyu_03,xianyu_07");
        out.put("message_rules_verified_scope", "real_device_message_and_chat_samples_2026-08-10");
        out.put("message_rules_verified", MessageExtractor.RULES_VERIFIED);
        out.put("history_cursor_scope", "bounded_process_memory_fingerprints_only");
        out.put("cross_restart_dedup_guaranteed", false);
        out.put("navigation_readback_semantics",
                "identity_match_plus_newly_observed_exact_outgoing_text_no_send");
        out.put("live_endpoint_verified", false);
        return out;
    }
}
