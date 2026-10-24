package com.feifei.phonebridge.plugin.xianyu;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 闲鱼消息列表和单会话聊天提取器。
 *
 * 规则仅使用 2026-08-10 登录测试号真实 observe 已确认的结构：消息页全宽
 * 可点击列表行、行内 bounds 子节点、聊天页左右头像/气泡、全宽时间或系统消息。
 * 原始聊天内容不进入仓库；测试夹具只保留脱敏后的结构形状。
 */
public final class MessageExtractor {

    private static final Pattern UNREAD = Pattern.compile("^未读(?:消息)?数(\\d{1,4})$");
    private static final Pattern TIME = Pattern.compile(
            "(?:\\d{4}-\\d{2}-\\d{2}\\s+\\d{1,2}:\\d{2}|"
                    + "\\d{2}-\\d{2}(?:\\s+\\d{1,2}:\\d{2})?|"
                    + "\\d{1,2}:\\d{2}|\\d+分钟前|今天|昨天|前天|星期[一二三四五六日天])"
    );
    private static final Set<String> SYSTEM_CONVERSATIONS = immutableSet(
            "通知消息", "互动消息", "热门活动", "闲鱼精选", "关注上新"
    );
    private static final Set<String> CONVERSATION_MARKERS = immutableSet(
            "红点提醒", "有新交易评价", "交易成功", "还没有新通知~"
    );
    private static final Set<String> CHAT_CHROME = immutableSet(
            "返回", "更多", "想跟TA说点什么...", "语音按钮", "表情按钮", "更多选择",
            "去评价", "再次购买", "送给Ta"
    );
    private static final String[] SYSTEM_MESSAGE_PREFIXES = {
            "我已拍下", "我已小刀", "我已成功小刀", "卖家已发货", "订单即将",
            "你已确认收货", "交易成功", "卖家交易风险", "快给ta一个评价",
            "温馨提示：", "我完成了评价", "可以送我闲鱼小红花吗"
    };

    private MessageExtractor() {
    }

    /** MESSAGES/CHAT 的结构规则已有真实页面依据；真机端点仍由结果字段单独标注。 */
    public static final boolean RULES_VERIFIED = true;

    public static JSONArray extractConversations(JSONObject observeJson) throws JSONException {
        if (XianYuPage.classify(observeJson) != XianYuPage.MESSAGES) {
            return new JSONArray();
        }
        return extractConversationNodes(UiNode.fromObserve(observeJson));
    }

    static JSONArray extractConversationNodes(UiNode[] nodes) throws JSONException {
        JSONArray out = new JSONArray();
        for (Conversation conversation : extractConversationModels(nodes)) {
            out.put(conversation.toJson());
        }
        return out;
    }

    /** Pure-Java production rule entry used by the deidentified replay fixture. */
    static List<Conversation> extractConversationModels(UiNode[] nodes) {
        List<Conversation> out = new ArrayList<>();
        if (nodes == null || nodes.length == 0) {
            return out;
        }
        int screenWidth = screenWidth(nodes);
        List<UiNode> rows = new ArrayList<>();
        for (UiNode node : nodes) {
            int width = node.bounds[2] - node.bounds[0];
            int height = node.bounds[3] - node.bounds[1];
            if (node.clickable && node.hasValidBounds()
                    && width >= screenWidth * 9 / 10
                    && height >= 180 && height <= 360
                    && node.bounds[1] >= 250) {
                rows.add(node);
            }
        }
        rows.sort(Comparator.comparingInt(node -> node.bounds[1]));
        Set<String> emitted = new HashSet<>();
        List<ConversationDraft> drafts = new ArrayList<>();
        for (UiNode row : rows) {
            List<UiNode> values = nodesInsideRow(nodes, row);
            UiNode nameNode = findConversationName(values, row, screenWidth);
            if (nameNode == null) {
                continue;
            }
            String displayName = clean(nameNode.bestText());
            String rowIdentity = displayName + "|" + row.bounds[0] + "," + row.bounds[1]
                    + "," + row.bounds[2] + "," + row.bounds[3];
            if (displayName.isEmpty() || SYSTEM_CONVERSATIONS.contains(displayName)
                    || !emitted.add(rowIdentity)) {
                continue;
            }
            String status = "";
            int unreadCount = 0;
            boolean unreadMarker = false;
            for (UiNode value : values) {
                String text = clean(value.bestText());
                Matcher unread = UNREAD.matcher(text);
                if (unread.matches()) {
                    unreadCount = Math.max(unreadCount, parseInt(unread.group(1)));
                    unreadMarker = true;
                } else if ("红点提醒".equals(text)) {
                    unreadMarker = true;
                } else if (CONVERSATION_MARKERS.contains(text) && status.isEmpty()) {
                    status = text;
                }
            }
            UiNode lastNode = findLastMessage(values, row, nameNode);
            String lastMessage = lastNode == null ? "" : clean(lastNode.bestText());
            String timeLabel = extractTimeLabel(clean(row.description));
            drafts.add(new ConversationDraft(
                    row,
                    nameNode,
                    displayName,
                    lastMessage,
                    timeLabel,
                    unreadCount,
                    unreadMarker,
                    status,
                    fingerprint("conversation", displayName)
            ));
        }
        Map<String, Integer> nameCounts = new HashMap<>();
        for (ConversationDraft draft : drafts) {
            nameCounts.put(draft.displayName,
                    nameCounts.containsKey(draft.displayName)
                            ? nameCounts.get(draft.displayName) + 1 : 1);
        }
        for (ConversationDraft draft : drafts) {
            boolean identityAmbiguous = nameCounts.get(draft.displayName) > 1;
            Locator locator = identityAmbiguous ? null
                    : findExactLocator(nodes, draft.row, draft.nameNode, draft.displayName);
            out.add(new Conversation(
                    draft.displayName,
                    draft.lastMessage,
                    draft.timeLabel,
                    draft.unreadCount,
                    draft.unreadMarker,
                    draft.status,
                    draft.fingerprint,
                    locator,
                    identityAmbiguous,
                    draft.row.bounds
            ));
        }
        return out;
    }

    public static JSONArray extractChatMessages(JSONObject observeJson) throws JSONException {
        if (XianYuPage.classify(observeJson) != XianYuPage.CHAT) {
            return new JSONArray();
        }
        return extractChatNodes(UiNode.fromObserve(observeJson));
    }

    static JSONArray extractChatNodes(UiNode[] nodes) throws JSONException {
        JSONArray out = new JSONArray();
        for (ChatMessage message : extractChatModels(nodes)) {
            out.put(message.toJson());
        }
        return out;
    }

    /** Pure-Java production rule entry used by the deidentified replay fixture. */
    static List<ChatMessage> extractChatModels(UiNode[] nodes) {
        List<ChatMessage> out = new ArrayList<>();
        if (nodes == null || nodes.length == 0) {
            return out;
        }
        int width = screenWidth(nodes);
        int height = screenHeight(nodes);
        List<UiNode> avatars = new ArrayList<>();
        List<UiNode> timeContainers = new ArrayList<>();
        List<UiNode> systemContainers = new ArrayList<>();
        for (UiNode node : nodes) {
            String description = clean(node.description);
            if ("头像".equals(description) && node.hasValidBounds()) {
                avatars.add(node);
            }
            if (isFullWidth(node, width) && !extractTimeLabel(description).isEmpty()) {
                timeContainers.add(node);
            }
            if (isFullWidth(node, width) && isSystemDescription(description)) {
                systemContainers.add(node);
            }
        }

        List<MessageCandidate> candidates = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (UiNode node : nodes) {
            String description = clean(node.description);
            if (description.isEmpty() || !node.hasValidBounds()
                    || CHAT_CHROME.contains(description)
                    || description.startsWith("闲鱼私聊")
                    || "头像".equals(description)
                    || description.contains("会员名:")
                    || node.bounds[1] >= height - 180
                    || node.depth < 12) {
                continue;
            }
            if (insideAnotherContainer(node, systemContainers)) {
                continue;
            }
            String type = messageType(description, node, width);
            String direction = "system";
            if (!"system".equals(type)) {
                direction = direction(node, avatars, width);
                if ("unknown".equals(direction)) {
                    continue;
                }
            }
            if (isTimeOrReceiptOnly(description) || isProductChrome(description, node)) {
                continue;
            }
            String key = type + "|" + direction + "|" + description + "|"
                    + node.bounds[0] + "," + node.bounds[1] + "," + node.bounds[2] + "," + node.bounds[3];
            if (!seen.add(key)) {
                continue;
            }
            candidates.add(new MessageCandidate(node, type, direction, description));
        }
        candidates = collapseOverlappingWrappers(candidates);
        candidates.sort(Comparator
                .comparingInt((MessageCandidate candidate) -> candidate.node.bounds[1])
                .thenComparingInt(candidate -> candidate.node.index));

        int order = 0;
        for (MessageCandidate candidate : candidates) {
            String timeLabel = timeFor(candidate.node, timeContainers);
            boolean read = readFor(candidate.node, timeContainers);
            String content = "text".equals(candidate.type) || "system".equals(candidate.type)
                    ? candidate.description : "";
            String mediaLabel = content.isEmpty() ? candidate.description : "";
            String material = content.isEmpty() ? mediaLabel : content;
            out.add(new ChatMessage(
                    order++,
                    candidate.direction,
                    candidate.type,
                    content,
                    mediaLabel,
                    timeLabel,
                    read ? "read" : "unknown",
                    fingerprint(
                            "message",
                            candidate.direction + "|" + candidate.type + "|" + material + "|" + timeLabel
                    ),
                    timeLabel.isEmpty()
                            ? "weak_content_direction"
                            : "medium_content_time_direction",
                    candidate.node.bounds[1] < 0 || candidate.node.bounds[3] > height,
                    candidate.node.bounds
            ));
        }
        return out;
    }

    public static JSONObject extractResult(JSONObject observeJson) throws JSONException {
        XianYuPage page = XianYuPage.classify(observeJson);
        JSONArray conversations = page == XianYuPage.MESSAGES
                ? extractConversations(observeJson) : new JSONArray();
        JSONArray messages = page == XianYuPage.CHAT
                ? extractChatMessages(observeJson) : new JSONArray();
        boolean covered = page == XianYuPage.MESSAGES || page == XianYuPage.CHAT;
        boolean truncated = observeJson != null && observeJson.optBoolean("truncated", false);

        JSONObject out = new JSONObject();
        out.put("success", true);
        out.put("page", page.name());
        out.put("rules_verified", covered && RULES_VERIFIED);
        out.put("verification_scope", covered
                ? "captured_real_device_message_and_chat_samples_2026-08-10"
                : "message_rules_not_applicable_to_current_page");
        out.put("live_endpoint_verified", false);
        out.put("snapshot_complete", !truncated);
        out.put("note", note(page, truncated));
        out.put("conversations", conversations);
        out.put("conversation_count", conversations.length());
        out.put("messages", messages);
        out.put("message_count", messages.length());
        if (page == XianYuPage.CHAT) {
            out.put("chat_context", chatContext(UiNode.fromObserve(observeJson)));
        }
        if (covered) {
            out.put("navigation_and_readback", new JSONObject()
                    .put("locator_rule", "unique_exact_resource_id_text_or_description")
                    .put("target_member_match_required_for_safe_reply", true)
                    .put("outgoing_readback_rule",
                            "newly_observed_after_confirmed_cursor_plus_nfkc_exact_outgoing_text")
                    .put("performs_input_or_send", false));
        }
        return out;
    }

    private static JSONObject chatContext(UiNode[] nodes) throws JSONException {
        ChatIdentity identity = extractChatIdentity(nodes);
        return new JSONObject()
                .put("display_name", identity.displayName)
                .put("member_name", identity.memberName)
                .put("identity_fingerprint", identity.identityFingerprint)
                .put("identity_quality", identity.identityQuality)
                .put("identity_confirmable", identity.identityConfirmable)
                .put("input_available", identity.inputAvailable)
                .put("history_scrollable", identity.historyScrollable)
                .put("navigation_status", identity.identityConfirmable
                        ? "member_identity_available_for_target_match"
                        : "identity_uncertain_member_missing");
    }

    static ChatIdentity extractChatIdentity(UiNode[] nodes) {
        String displayName = "";
        String memberName = "";
        boolean inputAvailable = false;
        boolean historyScrollable = false;
        if (nodes == null) {
            nodes = new UiNode[0];
        }
        for (UiNode node : nodes) {
            String description = clean(node.description);
            if (description.contains("会员名:")) {
                String[] lines = description.split("\\n", 2);
                displayName = clean(lines[0]);
                memberName = lines.length > 1
                        ? clean(lines[1].replace("会员名:", "")) : "";
            } else if (displayName.isEmpty() && node.clickable && node.bounds[1] >= 100
                    && node.bounds[3] <= 300 && !description.isEmpty()
                    && !"返回".equals(description) && !"更多".equals(description)) {
                displayName = description;
            }
            inputAvailable |= "想跟TA说点什么...".equals(description);
            historyScrollable |= node.scrollable && description.startsWith("闲鱼私聊");
        }
        String identityQuality;
        String identityFingerprint;
        if (!memberName.isEmpty()) {
            identityQuality = "medium_member_name";
            identityFingerprint = fingerprint("chat_identity_member", memberName);
        } else if (!displayName.isEmpty()) {
            identityQuality = "weak_display_name";
            identityFingerprint = fingerprint("chat_identity_display", displayName);
        } else {
            identityQuality = "unavailable";
            identityFingerprint = "";
        }
        return new ChatIdentity(
                displayName,
                memberName,
                identityFingerprint,
                identityQuality,
                !memberName.isEmpty(),
                inputAvailable,
                historyScrollable
        );
    }

    private static List<UiNode> nodesInsideRow(UiNode[] nodes, UiNode row) {
        List<UiNode> values = new ArrayList<>();
        for (UiNode node : nodes) {
            if (node.index == row.index || !node.hasValidBounds() || node.depth <= row.depth) {
                continue;
            }
            int centerX = node.centerX();
            int centerY = node.centerY();
            if (centerX >= row.bounds[0] && centerX <= row.bounds[2]
                    && centerY >= row.bounds[1] && centerY <= row.bounds[3]) {
                values.add(node);
            }
        }
        values.sort(Comparator
                .comparingInt((UiNode node) -> node.bounds[1])
                .thenComparingInt(node -> node.bounds[0])
                .thenComparingInt(node -> node.index));
        return values;
    }

    private static UiNode findConversationName(List<UiNode> values, UiNode row, int screenWidth) {
        int upperLimit = row.bounds[1] + (row.bounds[3] - row.bounds[1]) * 58 / 100;
        for (UiNode node : values) {
            String text = clean(node.bestText());
            if (node.bounds[0] >= screenWidth * 15 / 100
                    && node.bounds[1] <= upperLimit
                    && isConversationName(text)) {
                return node;
            }
        }
        return null;
    }

    private static UiNode findLastMessage(List<UiNode> values, UiNode row, UiNode nameNode) {
        int lowerStart = row.bounds[1] + (row.bounds[3] - row.bounds[1]) * 35 / 100;
        for (UiNode node : values) {
            String text = clean(node.bestText());
            if (node.index != nameNode.index && node.bounds[1] >= lowerStart
                    && !text.isEmpty() && !CONVERSATION_MARKERS.contains(text)
                    && !UNREAD.matcher(text).matches() && !SYSTEM_CONVERSATIONS.contains(text)
                    && !"图片".equals(text)) {
                return node;
            }
        }
        return null;
    }

    private static Locator findExactLocator(
            UiNode[] nodes,
            UiNode row,
            UiNode nameNode,
            String displayName
    ) {
        if (nodes == null || row == null || nameNode == null || !row.clickable
                || !row.containsCenter(nameNode)) {
            return null;
        }
        String resourceId = clean(nameNode.resourceId);
        if (!resourceId.isEmpty() && resourceId.length() <= 500
                && countExact(nodes, "resource_id", resourceId) == 1) {
            return new Locator("resource_id", resourceId);
        }
        if (displayName.length() <= 500
                && displayName.equals(clean(nameNode.text))
                && countExact(nodes, "text", displayName) == 1) {
            return new Locator("text", displayName);
        }
        if (displayName.length() <= 500
                && displayName.equals(clean(nameNode.description))
                && countExact(nodes, "description", displayName) == 1) {
            return new Locator("description", displayName);
        }
        return null;
    }

    private static int countExact(UiNode[] nodes, String kind, String expected) {
        int count = 0;
        for (UiNode node : nodes) {
            String actual;
            if ("resource_id".equals(kind)) {
                actual = clean(node.resourceId);
            } else if ("text".equals(kind)) {
                actual = clean(node.text);
            } else {
                actual = clean(node.description);
            }
            if (expected.equals(actual)) {
                count++;
            }
        }
        return count;
    }

    private static boolean isConversationName(String text) {
        return !text.isEmpty() && !CONVERSATION_MARKERS.contains(text)
                && !UNREAD.matcher(text).matches()
                && !text.startsWith("[") && !text.contains("待领")
                && !text.contains("消息以前") && !TIME.matcher(text).matches();
    }

    private static String messageType(String description, UiNode node, int width) {
        if (isSystemDescription(description) || (isFullWidth(node, width) && node.depth <= 12)) {
            return "system";
        }
        if (description.startsWith("图片消息") || description.matches("^图片[，,].*")) {
            return "image";
        }
        if (description.startsWith("[语音]") || description.matches("^语音[，, ].*")) {
            return "voice";
        }
        return "text";
    }

    private static String direction(UiNode message, List<UiNode> avatars, int width) {
        UiNode nearest = null;
        int nearestDistance = Integer.MAX_VALUE;
        for (UiNode avatar : avatars) {
            int distance = Math.abs(avatar.centerY() - message.centerY());
            boolean overlaps = avatar.bounds[1] <= message.bounds[3]
                    && avatar.bounds[3] >= message.bounds[1];
            int allowed = Math.min(420, Math.max(180,
                    (message.bounds[3] - message.bounds[1]) / 2 + 130));
            if ((overlaps || distance <= allowed) && distance < nearestDistance) {
                nearest = avatar;
                nearestDistance = distance;
            }
        }
        if (nearest != null) {
            return nearest.centerX() < width / 2 ? "incoming" : "outgoing";
        }
        if (message.bounds[0] <= width * 20 / 100) {
            return "incoming";
        }
        if (message.bounds[0] >= width * 45 / 100 || message.bounds[2] >= width * 80 / 100) {
            return "outgoing";
        }
        return "unknown";
    }

    private static boolean insideAnotherContainer(UiNode node, List<UiNode> containers) {
        for (UiNode container : containers) {
            if (container.index != node.index && node.depth > container.depth
                    && container.containsCenter(node)) {
                return true;
            }
        }
        return false;
    }

    private static List<MessageCandidate> collapseOverlappingWrappers(
            List<MessageCandidate> candidates) {
        List<MessageCandidate> collapsed = new ArrayList<>();
        for (MessageCandidate candidate : candidates) {
            int duplicateIndex = -1;
            for (int index = 0; index < collapsed.size(); index++) {
                MessageCandidate existing = collapsed.get(index);
                if (candidate.type.equals(existing.type)
                        && candidate.direction.equals(existing.direction)
                        && candidate.description.equals(existing.description)
                        && (candidate.node.containsCenter(existing.node)
                        || existing.node.containsCenter(candidate.node))) {
                    duplicateIndex = index;
                    break;
                }
            }
            if (duplicateIndex < 0) {
                collapsed.add(candidate);
                continue;
            }
            MessageCandidate existing = collapsed.get(duplicateIndex);
            if (candidate.node.boundsArea() < existing.node.boundsArea()
                    || (candidate.node.boundsArea() == existing.node.boundsArea()
                    && candidate.node.depth > existing.node.depth)) {
                collapsed.set(duplicateIndex, candidate);
            }
        }
        return collapsed;
    }

    private static String timeFor(UiNode message, List<UiNode> containers) {
        UiNode best = containingContainer(message, containers);
        return best == null ? "" : extractTimeLabel(clean(best.description));
    }

    private static boolean readFor(UiNode message, List<UiNode> containers) {
        UiNode best = containingContainer(message, containers);
        return best != null && clean(best.description).contains("已读");
    }

    private static UiNode containingContainer(UiNode message, List<UiNode> containers) {
        UiNode best = null;
        long bestArea = Long.MAX_VALUE;
        for (UiNode container : containers) {
            if (container.bounds[1] <= message.centerY() && container.bounds[3] >= message.centerY()
                    && container.boundsArea() < bestArea) {
                best = container;
                bestArea = container.boundsArea();
            }
        }
        return best;
    }

    private static boolean isFullWidth(UiNode node, int width) {
        return node.hasValidBounds() && node.bounds[0] <= 24 && node.bounds[2] >= width - 24;
    }

    private static boolean isSystemDescription(String description) {
        for (String prefix : SYSTEM_MESSAGE_PREFIXES) {
            if (description.startsWith(prefix)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isTimeOrReceiptOnly(String description) {
        String withoutTime = TIME.matcher(description).replaceAll("").replace("已读", "").trim();
        return withoutTime.isEmpty();
    }

    private static boolean isProductChrome(String description, UiNode node) {
        return node.depth <= 10 && (description.startsWith("商品")
                || description.startsWith("¥") || description.contains("含运费"));
    }

    private static String extractTimeLabel(String value) {
        Matcher matcher = TIME.matcher(value == null ? "" : value);
        return matcher.find() ? matcher.group() : "";
    }

    private static int screenWidth(UiNode[] nodes) {
        int width = 1;
        for (UiNode node : nodes) {
            width = Math.max(width, node.bounds[2]);
        }
        return width;
    }

    private static int screenHeight(UiNode[] nodes) {
        int height = 1;
        for (UiNode node : nodes) {
            height = Math.max(height, node.bounds[3]);
        }
        return height;
    }

    private static int parseInt(String value) {
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    private static Set<String> immutableSet(String... values) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(values)));
    }

    static String normalize(String value) {
        if (value == null) {
            return "";
        }
        return Normalizer.normalize(value, Normalizer.Form.NFKC).trim();
    }

    private static String clean(String value) {
        return normalize(value);
    }

    static String stableFingerprint(String domain, String material) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest((domain + "|" + clean(material))
                    .getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (int index = 0; index < 12; index++) {
                hex.append(String.format(Locale.ROOT, "%02x", bytes[index] & 0xff));
            }
            return hex.toString();
        } catch (Exception impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    private static String fingerprint(String domain, String material) {
        return stableFingerprint(domain, material);
    }

    private static JSONArray boundsJson(int[] bounds) {
        JSONArray out = new JSONArray();
        for (int value : bounds) {
            out.put(value);
        }
        return out;
    }

    private static String note(XianYuPage page, boolean truncated) {
        if (page == XianYuPage.MESSAGES) {
            return truncated
                    ? "message list snapshot truncated; candidates may be incomplete and require one stable retry"
                    : "message list extracted from verified accessibility structure";
        }
        if (page == XianYuPage.CHAT) {
            return truncated
                    ? "chat snapshot truncated; history candidates may be incomplete and require one stable retry"
                    : "chat messages extracted from verified accessibility structure";
        }
        return "message extraction not applicable to current page";
    }

    private static final class MessageCandidate {
        final UiNode node;
        final String type;
        final String direction;
        final String description;

        MessageCandidate(UiNode node, String type, String direction, String description) {
            this.node = node;
            this.type = type;
            this.direction = direction;
            this.description = description;
        }
    }

    private static final class ConversationDraft {
        final UiNode row;
        final UiNode nameNode;
        final String displayName;
        final String lastMessage;
        final String timeLabel;
        final int unreadCount;
        final boolean unreadMarker;
        final String status;
        final String fingerprint;

        ConversationDraft(UiNode row, UiNode nameNode, String displayName, String lastMessage,
                          String timeLabel, int unreadCount, boolean unreadMarker, String status,
                          String fingerprint) {
            this.row = row;
            this.nameNode = nameNode;
            this.displayName = displayName;
            this.lastMessage = lastMessage;
            this.timeLabel = timeLabel;
            this.unreadCount = unreadCount;
            this.unreadMarker = unreadMarker;
            this.status = status;
            this.fingerprint = fingerprint;
        }
    }

    static final class Locator {
        final String kind;
        final String value;

        Locator(String kind, String value) {
            this.kind = kind;
            this.value = value;
        }
    }

    static final class ChatIdentity {
        final String displayName;
        final String memberName;
        final String identityFingerprint;
        final String identityQuality;
        final boolean identityConfirmable;
        final boolean inputAvailable;
        final boolean historyScrollable;

        ChatIdentity(String displayName, String memberName, String identityFingerprint,
                     String identityQuality, boolean identityConfirmable,
                     boolean inputAvailable, boolean historyScrollable) {
            this.displayName = displayName;
            this.memberName = memberName;
            this.identityFingerprint = identityFingerprint;
            this.identityQuality = identityQuality;
            this.identityConfirmable = identityConfirmable;
            this.inputAvailable = inputAvailable;
            this.historyScrollable = historyScrollable;
        }
    }

    static final class Conversation {
        final String displayName;
        final String lastMessage;
        final String timeLabel;
        final int unreadCount;
        final boolean unreadMarker;
        final String status;
        final String fingerprint;
        final Locator locator;
        final boolean locatorAvailable;
        final boolean identityAmbiguous;
        final int[] bounds;

        Conversation(String displayName, String lastMessage, String timeLabel, int unreadCount,
                     boolean unreadMarker, String status, String fingerprint,
                     Locator locator, boolean identityAmbiguous, int[] bounds) {
            this.displayName = displayName;
            this.lastMessage = lastMessage;
            this.timeLabel = timeLabel;
            this.unreadCount = unreadCount;
            this.unreadMarker = unreadMarker;
            this.status = status;
            this.fingerprint = fingerprint;
            this.locator = locator;
            this.locatorAvailable = locator != null;
            this.identityAmbiguous = identityAmbiguous;
            this.bounds = bounds.clone();
        }

        JSONObject toJson() throws JSONException {
            return new JSONObject()
                    .put("display_name", displayName)
                    .put("last_message", lastMessage)
                    .put("time_label", timeLabel)
                    .put("unread_count", unreadCount)
                    .put("unread_marker", unreadMarker)
                    .put("status", status)
                    .put("conversation_fingerprint", fingerprint)
                    .put("fingerprint_scope", "display_name_only")
                    .put("fingerprint_quality", "weak_until_chat_member_id_is_observed")
                    .put("identity_ambiguous", identityAmbiguous)
                    .put("navigation_status", identityAmbiguous
                            ? "same_name_conflict"
                            : locatorAvailable ? "locator_ready" : "locator_unavailable")
                    .put("locator_available", locatorAvailable)
                    .put("locator", locatorAvailable
                            ? new JSONObject()
                                    .put("kind", locator.kind)
                                    .put("value", locator.value)
                            : new JSONObject()
                                    .put("kind", "unavailable")
                                    .put("value", ""))
                    .put("bounds", boundsJson(bounds));
        }
    }

    static final class ChatMessage {
        final int snapshotOrder;
        final String direction;
        final String type;
        final String text;
        final String mediaLabel;
        final String timeLabel;
        final String readState;
        final String fingerprint;
        final String fingerprintQuality;
        final boolean partiallyVisible;
        final int[] bounds;

        ChatMessage(int snapshotOrder, String direction, String type, String text,
                    String mediaLabel, String timeLabel, String readState, String fingerprint,
                    String fingerprintQuality, boolean partiallyVisible, int[] bounds) {
            this.snapshotOrder = snapshotOrder;
            this.direction = direction;
            this.type = type;
            this.text = text;
            this.mediaLabel = mediaLabel;
            this.timeLabel = timeLabel;
            this.readState = readState;
            this.fingerprint = fingerprint;
            this.fingerprintQuality = fingerprintQuality;
            this.partiallyVisible = partiallyVisible;
            this.bounds = bounds.clone();
        }

        JSONObject toJson() throws JSONException {
            return new JSONObject()
                    .put("snapshot_order", snapshotOrder)
                    .put("direction", direction)
                    .put("type", type)
                    .put("text", text)
                    .put("media_label", mediaLabel)
                    .put("time_label", timeLabel)
                    .put("read_state", readState)
                    .put("message_fingerprint", fingerprint)
                    .put("fingerprint_quality", fingerprintQuality)
                    .put("partially_visible", partiallyVisible)
                    .put("source", "accessibility_content_description")
                    .put("bounds", boundsJson(bounds));
        }
    }
}
