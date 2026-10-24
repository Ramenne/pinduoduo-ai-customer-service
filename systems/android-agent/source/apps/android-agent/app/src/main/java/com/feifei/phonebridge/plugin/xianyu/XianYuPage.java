package com.feifei.phonebridge.plugin.xianyu;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * 闲鱼页面状态枚举 + 节点匹配规则。
 *
 * LOGIN/HOME 规则来自 2026-08-07 真机样本；MESSAGES/CHAT 规则来自
 * 2026-08-10 登录测试号的真实云端 observe 探索。ITEM_DETAIL/LAUNCH 仍不猜测。
 *
 * 判定优先级（高在前，先命中先返回）：
 *   LOGIN > CHAT > MESSAGES > HOME > OTHER / UNKNOWN
 */
public enum XianYuPage {

    /** 启动页 / 开屏 */
    LAUNCH,
    /** 登录页（手机号 + 验证码 SSO 登录） */
    LOGIN,
    /** 首页（底部 tab：闲鱼/同城/卖闲置/消息/我的） */
    HOME,
    /** 消息会话列表页 */
    MESSAGES,
    /** 聊天页（单会话） */
    CHAT,
    /** 商品详情页 */
    ITEM_DETAIL,
    /** 闲鱼 App 内，但不在上述已知页 */
    OTHER,
    /** 非闲鱼 App，或 UI 树为空 */
    UNKNOWN;

    /** 闲鱼包名，实测确认 */
    public static final String PACKAGE = "com.taobao.idlefish";

    /** 登录页特征 resource_id 前缀（阿里系 SSO 登录，实测 xianyu_02/04） */
    private static final String[] LOGIN_RID = {
            "aliuser_login_mobile_et",
            "aliuser_register_sms_code_et",
            "aliuser_login_send_smscode_btn",
            "ssoLoginView",
            "welcome_text"
    };

    /** 首页特征：底部 tab 文案（可点击 FrameLayout，实测 xianyu_07 节点 25~29） */
    private static final String[] HOME_TABS = {"闲鱼", "同城", "卖闲置", "消息", "我的"};

    /**
     * 从 observe() 返回的 UI 树 JSON 判定页面状态。
     *
     * @param observeJson observe 端点原始返回（含 package/nodes/truncated）
     * @return 页面状态，永不为 null
     */
    public static XianYuPage classify(JSONObject observeJson) {
        if (observeJson == null) {
            return UNKNOWN;
        }
        String pkg = observeJson.optString("package", "");
        return classify(pkg, UiNode.fromObserve(observeJson));
    }

    /** 纯节点判定入口，供设备样本回放直接复用生产规则。 */
    static XianYuPage classify(String pkg, UiNode[] nodes) {
        if (!PACKAGE.equals(pkg)) {
            return UNKNOWN;
        }
        if (nodes == null || nodes.length == 0) {
            return UNKNOWN;
        }

        // 1. 登录页：SSO 登录特征 resource_id 命中任一即判定（最优先，防误进）
        for (UiNode n : nodes) {
            if (n.resourceId == null) continue;
            for (String rid : LOGIN_RID) {
                if (n.resourceId.contains(rid)) {
                    return LOGIN;
                }
            }
        }

        // 2. 单会话聊天：三个真实会话均同时暴露私聊根语义与输入区提示。
        if (hasDescriptionPrefix(nodes, "闲鱼私聊")
                && hasExactDescription(nodes, "想跟TA说点什么...")
                && hasExactDescription(nodes, "返回")) {
            return CHAT;
        }

        // 3. 消息列表：搜索入口与清除未读仅在消息页顶部同时出现。
        if (hasExactDescription(nodes, "搜索聊天记录/联系人/服务号")
                && hasExactDescription(nodes, "清除未读")) {
            return MESSAGES;
        }

        // 4. 首页：底部 tab 命中 >=4 个（卖闲置 + 闲鱼/同城/消息/我的）
        int tabHits = 0;
        for (String tab : HOME_TABS) {
            for (UiNode n : nodes) {
                if (n.clickable && tab.equals(n.bestText())) {
                    tabHits++;
                    break;
                }
                // tab 的"选中状态"desc 形式："闲鱼，未读消息数0，选中状态"
                if (n.clickable && n.description != null && n.description.startsWith(tab + "，")) {
                    tabHits++;
                    break;
                }
            }
        }
        if (tabHits >= 4) {
            return HOME;
        }

        // 未覆盖页面不猜测，等待登录后补真实样本。
        return OTHER;
    }

    public static boolean isRuleVerified(XianYuPage page) {
        return page == LOGIN || page == HOME || page == MESSAGES || page == CHAT;
    }

    private static boolean hasExactDescription(UiNode[] nodes, String expected) {
        for (UiNode node : nodes) {
            if (expected.equals(node.description.trim())) {
                return true;
            }
        }
        return false;
    }

    private static boolean hasDescriptionPrefix(UiNode[] nodes, String prefix) {
        for (UiNode node : nodes) {
            if (node.description.trim().startsWith(prefix)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 包装为 JSON 结果，供 HTTP 层直接返回。
     */
    public static JSONObject classifyResult(JSONObject observeJson) throws JSONException {
        XianYuPage page = classify(observeJson);
        JSONObject out = new JSONObject();
        out.put("success", true);
        out.put("page", page.name());
        out.put("rules_verified", isRuleVerified(page));
        String verificationScope = "no_verified_rule_for_this_page";
        if (page == LOGIN || page == HOME) {
            verificationScope = "captured_real_device_samples_2026-08-07";
        } else if (page == MESSAGES || page == CHAT) {
            verificationScope = "captured_real_device_samples_2026-08-10";
        }
        out.put("verification_scope", verificationScope);
        out.put("live_endpoint_verified", false);
        out.put("package", observeJson == null ? "" : observeJson.optString("package", ""));
        out.put("truncated", observeJson != null && observeJson.optBoolean("truncated", false));
        out.put("node_count", observeJson == null ? 0 : observeJson.optInt("node_count", 0));
        return out;
    }
}
