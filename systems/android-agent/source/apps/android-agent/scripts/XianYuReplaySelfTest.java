package com.feifei.phonebridge.plugin.xianyu;

import com.feifei.phonebridge.plugin.messaging.MessagingPlatformAdapter;
import com.feifei.phonebridge.plugin.messaging.MessagingPlatformRegistry;
import com.feifei.phonebridge.plugin.messaging.MessagingSnapshotContract;

import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

/**
 * PC 端样本回放入口。输入由 test-xianyu-replay.ps1 将 JSON 转成 TSV，
 * 这里直接调用生产代码的纯节点规则，不复制页面/商品算法。
 */
public final class XianYuReplaySelfTest {
    private XianYuReplaySelfTest() {
    }

    public static void main(String[] args) throws Exception {
        verifyBoundaryGuards();
        verifyMessageAndChatRules();
        verifyHistoryMergeRules();
        verifyNavigationAndReadbackRules();
        verifyMessagingPlatformContract();
        if (args.length != 1) {
            throw new IllegalArgumentException("Expected one TSV snapshot path");
        }
        String packageName = "";
        List<UiNode> nodes = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(Path.of(args[0]), StandardCharsets.UTF_8)) {
            String line;
            while ((line = reader.readLine()) != null) {
                String[] parts = line.split("\\t", -1);
                if (parts.length == 2 && "META".equals(parts[0])) {
                    packageName = decode(parts[1]);
                } else if (parts.length == 15 && "NODE".equals(parts[0])) {
                    nodes.add(UiNode.fromValues(
                            Integer.parseInt(parts[1]),
                            Integer.parseInt(parts[2]),
                            decode(parts[3]),
                            decode(parts[4]),
                            decode(parts[5]),
                            decode(parts[6]),
                            Boolean.parseBoolean(parts[7]),
                            Boolean.parseBoolean(parts[8]),
                            Boolean.parseBoolean(parts[9]),
                            Boolean.parseBoolean(parts[10]),
                            new int[]{
                                    Integer.parseInt(parts[11]),
                                    Integer.parseInt(parts[12]),
                                    Integer.parseInt(parts[13]),
                                    Integer.parseInt(parts[14])
                            }
                    ));
                }
            }
        }

        UiNode[] array = nodes.toArray(new UiNode[0]);
        XianYuPage page = XianYuPage.classify(packageName, array);
        ItemCardExtractor.Extraction extraction = page == XianYuPage.HOME
                ? ItemCardExtractor.extractNodes(array)
                : new ItemCardExtractor.Extraction(new ArrayList<>(), 0);
        System.out.println("PAGE\t" + page.name());
        System.out.println("SUMMARY\t" + extraction.cards.size() + "\t" + extraction.incompleteCount);
        for (ItemCardExtractor.ItemCard card : extraction.cards) {
            System.out.println("CARD\t" + card.anchorIndex + "\t" + encode(card.title)
                    + "\t" + card.price + "\t" + encode(card.seller));
        }
    }

    private static void verifyMessagingPlatformContract() {
        MessagingPlatformAdapter adapter = MessagingPlatformRegistry.findByPlatform("xianyu");
        require(adapter != null, "the real Xianyu adapter must be registered");
        require(adapter == MessagingPlatformRegistry.findByTargetApp(XianYuPage.PACKAGE),
                "platform and target-app lookup must resolve the same adapter");
        require(MessagingPlatformRegistry.findByPlatform("taobao") == null,
                "future platforms must not be advertised before a real adapter exists");
        require(MessagingSnapshotContract.NAME.equals("oin.messaging.snapshot")
                        && MessagingSnapshotContract.VERSION == 1,
                "stable messaging snapshot contract must remain version 1");
        require("xianyu.android.accessibility".equals(adapter.adapterId())
                        && "android_accessibility".equals(adapter.runtimeKind())
                        && XianYuPage.PACKAGE.equals(adapter.targetAppId()),
                "the registered adapter must expose its concrete platform/runtime/target");
    }

    /** 对 null、空节点、缺失 bounds 和孤立价格锚点做生产纯规则冒烟测试。 */
    private static void verifyBoundaryGuards() {
        require(XianYuPage.classify(null, null) == XianYuPage.UNKNOWN,
                "null package/nodes must classify UNKNOWN");
        require(ItemCardExtractor.extractNodes(null).cards.isEmpty(),
                "null nodes must return no cards");

        UiNode missingFields = UiNode.fromValues(
                -1, 0, null, null, null, null,
                false, false, false, true, null);
        require(!missingFields.hasValidBounds(), "missing bounds must be invalid but safe");
        require(!missingFields.textContains(null) && !missingFields.ridContains(null),
                "null search fragments must not match or throw");
        require(XianYuPage.classify(XianYuPage.PACKAGE, new UiNode[]{missingFields})
                        == XianYuPage.OTHER,
                "empty in-app node must classify OTHER");

        UiNode orphanPrice = UiNode.fromValues(
                1, 1, "android.widget.TextView", "", "¥", "",
                false, false, false, true, null);
        ItemCardExtractor.Extraction orphan =
                ItemCardExtractor.extractNodes(new UiNode[]{orphanPrice});
        require(orphan.cards.isEmpty() && orphan.incompleteCount == 1,
                "orphan price anchor must be reported incomplete without throwing");
    }

    /**
     * Deidentified structural replay based on the 2026-08-10 real-phone exploration.
     * No customer message, member id, order id, or product text is retained here.
     */
    private static void verifyMessageAndChatRules() {
        UiNode[] messageList = new UiNode[]{
                node(1, 1, "android.widget.FrameLayout", "", "", false, false,
                        false, new int[]{0, 0, 1080, 2400}),
                node(2, 4, "android.widget.Button", "", "清除未读", true, false,
                        false, new int[]{900, 100, 1060, 180}),
                node(3, 4, "android.widget.TextView", "", "搜索聊天记录/联系人/服务号",
                        true, false, false, new int[]{40, 190, 1040, 290}),
                node(4, 5, "android.widget.ScrollView", "", "消息会话列表",
                        false, false, true, new int[]{0, 300, 1080, 2200}),

                node(10, 6, "android.view.View", "", "买家甲\n08-10 14:30",
                        true, false, false, new int[]{0, 320, 1080, 540}),
                node(11, 7, "android.widget.TextView", "买家甲", "", true, false,
                        false, new int[]{180, 340, 620, 400}),
                node(12, 7, "android.widget.TextView", "请问还在吗", "", false, false,
                        false, new int[]{180, 420, 780, 490}),
                node(13, 7, "android.widget.TextView", "", "未读数2", false, false,
                        false, new int[]{910, 350, 1010, 410}),

                node(20, 6, "android.view.View", "", "买家乙\n昨天",
                        true, false, false, new int[]{0, 550, 1080, 770}),
                node(21, 7, "android.widget.TextView", "买家乙", "", true, false,
                        false, new int[]{180, 570, 620, 630}),
                node(22, 7, "android.widget.TextView", "", "交易成功", false, false,
                        false, new int[]{180, 640, 500, 690}),
                node(23, 7, "android.widget.TextView", "好的", "", false, false,
                        false, new int[]{180, 700, 780, 750}),

                node(30, 6, "android.view.View", "", "通知消息\n今天",
                        true, false, false, new int[]{0, 780, 1080, 1000}),
                node(31, 7, "android.widget.TextView", "通知消息", "", false, false,
                        false, new int[]{180, 800, 620, 860}),
                node(32, 7, "android.widget.TextView", "系统通知", "", false, false,
                        false, new int[]{180, 900, 780, 960})
        };
        require(XianYuPage.classify(XianYuPage.PACKAGE, messageList) == XianYuPage.MESSAGES,
                "verified message-list anchors must classify MESSAGES");
        List<MessageExtractor.Conversation> conversations =
                MessageExtractor.extractConversationModels(messageList);
        require(conversations.size() == 2,
                "system rows must be excluded and two ordinary conversations extracted");
        MessageExtractor.Conversation first = conversations.get(0);
        require("买家甲".equals(first.displayName) && "请问还在吗".equals(first.lastMessage),
                "ordinary conversation name and last message must be extracted");
        require(first.unreadCount == 2 && first.unreadMarker,
                "numeric unread marker must be preserved");
        require(first.locatorAvailable,
                "conversation locator may be advertised only when an exact clickable node exists");
        require(first.locator != null && "text".equals(first.locator.kind)
                        && "买家甲".equals(first.locator.value),
                "conversation locator must declare the exact selector field it actually matched");
        require(!first.fingerprint.isEmpty(), "conversation fingerprint must be non-empty");
        MessageExtractor.Conversation second = conversations.get(1);
        require("交易成功".equals(second.status) && "好的".equals(second.lastMessage),
                "status must not replace the ordinary last-message text");

        UiNode[] duplicateNames = new UiNode[]{
                node(40, 6, "android.view.View", "", "同名买家\n14:30",
                        true, false, false, new int[]{0, 320, 1080, 540}),
                node(41, 7, "android.widget.TextView", "同名买家", "",
                        false, false, false, new int[]{180, 340, 620, 400}),
                node(42, 7, "android.widget.TextView", "消息一", "",
                        false, false, false, new int[]{180, 420, 780, 490}),
                node(50, 6, "android.view.View", "", "同名买家\n14:31",
                        true, false, false, new int[]{0, 550, 1080, 770}),
                node(51, 7, "android.widget.TextView", "同名买家", "",
                        false, false, false, new int[]{180, 570, 620, 630}),
                node(52, 7, "android.widget.TextView", "消息二", "",
                        false, false, false, new int[]{180, 650, 780, 720})
        };
        List<MessageExtractor.Conversation> ambiguousConversations =
                MessageExtractor.extractConversationModels(duplicateNames);
        require(ambiguousConversations.size() == 2
                        && ambiguousConversations.get(0).identityAmbiguous
                        && ambiguousConversations.get(1).identityAmbiguous
                        && !ambiguousConversations.get(0).locatorAvailable
                        && !ambiguousConversations.get(1).locatorAvailable,
                "same-name rows must be preserved but must not advertise an unsafe locator");

        UiNode[] chat = new UiNode[]{
                node(100, 1, "android.widget.FrameLayout", "", "", false, false,
                        false, new int[]{0, 0, 1080, 2400}),
                node(101, 4, "android.widget.Button", "", "返回", true, false,
                        false, new int[]{10, 90, 120, 190}),
                node(102, 4, "android.widget.TextView", "", "买家甲\n会员名：masked_test",
                        true, false, false, new int[]{160, 90, 780, 200}),
                node(103, 5, "android.widget.ScrollView", "", "闲鱼私聊，买家甲",
                        false, false, true, new int[]{0, 220, 1080, 2160}),
                node(104, 8, "android.widget.EditText", "", "想跟TA说点什么...",
                        true, true, false, new int[]{120, 2200, 860, 2300}),

                node(110, 10, "android.view.View", "", "08-10 14:30",
                        false, false, false, new int[]{0, 420, 1080, 650}),
                node(111, 14, "android.widget.ImageView", "", "头像", false, false,
                        false, new int[]{20, 490, 100, 570}),
                node(113, 13, "android.view.View", "", "请问还在吗",
                        false, false, false, new int[]{130, 450, 740, 630}),
                node(112, 14, "android.widget.TextView", "", "请问还在吗",
                        false, false, false, new int[]{160, 480, 700, 600}),

                node(120, 10, "android.view.View", "", "08-10 14:31\n已读",
                        false, false, false, new int[]{0, 680, 1080, 930}),
                node(121, 14, "android.widget.ImageView", "", "头像", false, false,
                        false, new int[]{980, 790, 1060, 870}),
                node(122, 14, "android.widget.TextView", "", "您好，可以的",
                        false, false, false, new int[]{480, 760, 940, 880}),

                node(130, 10, "android.view.View", "", "08-10 14:32",
                        false, false, false, new int[]{0, 960, 1080, 1240}),
                node(131, 14, "android.widget.ImageView", "", "头像", false, false,
                        false, new int[]{20, 1060, 100, 1140}),
                node(132, 14, "android.widget.ImageView", "", "图片消息，查看大图",
                        true, false, false, new int[]{160, 1020, 600, 1200}),

                node(140, 12, "android.widget.TextView", "", "交易成功，请查看订单",
                        false, false, false, new int[]{0, 1320, 1080, 1480}),
                node(141, 14, "android.widget.TextView", "", "订单详情",
                        false, false, false, new int[]{260, 1360, 820, 1430})
        };
        require(XianYuPage.classify(XianYuPage.PACKAGE, chat) == XianYuPage.CHAT,
                "verified private-chat anchors must classify CHAT");
        List<MessageExtractor.ChatMessage> messages = MessageExtractor.extractChatModels(chat);
        require(messages.size() == 4, "chat fixture must yield text, image, and system messages");
        require("incoming".equals(messages.get(0).direction)
                        && "text".equals(messages.get(0).type)
                        && "请问还在吗".equals(messages.get(0).text),
                "left-avatar text must be classified incoming");
        require("outgoing".equals(messages.get(1).direction)
                        && "read".equals(messages.get(1).readState),
                "right-avatar text and containing receipt must be classified outgoing/read");
        require("image".equals(messages.get(2).type)
                        && messages.get(2).text.isEmpty()
                        && !messages.get(2).mediaLabel.isEmpty(),
                "image accessibility label must remain metadata, not fabricated text");
        require("system".equals(messages.get(3).direction)
                        && "system".equals(messages.get(3).type),
                "full-width verified order notice must be classified system");
        List<MessageExtractor.ChatMessage> repeated = MessageExtractor.extractChatModels(chat);
        require(messages.get(0).fingerprint.equals(repeated.get(0).fingerprint),
                "same visible message must keep a stable fingerprint across snapshots");
        require(MessageExtractor.RULES_VERIFIED,
                "message rules may be true only while these real-shape fixtures pass");
        MessageExtractor.ChatIdentity identity = MessageExtractor.extractChatIdentity(chat);
        require("买家甲".equals(identity.displayName)
                        && "masked_test".equals(identity.memberName)
                        && "medium_member_name".equals(identity.identityQuality)
                        && identity.identityConfirmable
                        && !identity.identityFingerprint.isEmpty(),
                "chat identity mismatch: display=[" + identity.displayName
                        + "] member=[" + identity.memberName
                        + "] quality=[" + identity.identityQuality
                        + "] confirmable=" + identity.identityConfirmable
                        + " fingerprint=[" + identity.identityFingerprint + "]");

        require(!XianyuSnapshotRetryPolicy.shouldRetry(true, false, 10_000L),
                "complete snapshots must never be repeated");
        require(!XianyuSnapshotRetryPolicy.shouldRetry(true, true, 2_999L),
                "retry must not overrun the remaining task budget");
        require(XianyuSnapshotRetryPolicy.shouldRetry(true, true, 3_000L),
                "one stable retry is allowed at the exact budget boundary");
        require(XianyuSnapshotRetryPolicy.shouldUseSecond(
                        "MESSAGES", 63, true, "MESSAGES", 106, false),
                "same-page complete retry must replace a truncated first snapshot");
        require(!XianyuSnapshotRetryPolicy.shouldUseSecond(
                        "MESSAGES", 63, true, "CHAT", 80, false),
                "page changes during stabilization must not replace the atomic first snapshot");
        require(!XianyuSnapshotRetryPolicy.shouldUseSecond(
                        "MESSAGES", 106, true, "MESSAGES", 63, true),
                "a smaller still-truncated retry must not replace the stronger first snapshot");
    }

    private static void verifyHistoryMergeRules() {
        List<MessageHistoryMerger.Entry> initial = java.util.Arrays.asList(
                historyEntry("a", "incoming"),
                historyEntry("b", "outgoing")
        );
        MessageHistoryMerger.MergeResult baseline = MessageHistoryMerger.merge(
                "conversation-a", "medium_member_name", true, initial, null);
        require("baseline_established".equals(baseline.status)
                        && baseline.newCandidateCount == 0
                        && baseline.nextCursor != null,
                "the first complete snapshot must establish a non-emitting baseline");

        MessageHistoryMerger.MergeResult duplicate = MessageHistoryMerger.merge(
                "conversation-a", "medium_member_name", true,
                initial, baseline.nextCursor);
        require("duplicate_snapshot".equals(duplicate.status)
                        && duplicate.newCandidateCount == 0
                        && noNewCandidates(duplicate),
                "an identical snapshot must never emit the same incoming again");

        List<MessageHistoryMerger.Entry> withNewIncoming = java.util.Arrays.asList(
                historyEntry("a", "incoming"),
                historyEntry("b", "outgoing"),
                historyEntry("c", "incoming")
        );
        MessageHistoryMerger.MergeResult oneNew = MessageHistoryMerger.merge(
                "conversation-a", "medium_member_name", true,
                withNewIncoming, baseline.nextCursor);
        require(oneNew.newCandidateCount == 1
                        && oneNew.annotations.get(2).newCandidate
                        && !oneNew.annotations.get(2).uncertain,
                "one incoming after the confirmed boundary must emit exactly once");

        List<MessageHistoryMerger.Entry> repeatedInitial = java.util.Arrays.asList(
                historyEntry("anchor", "outgoing"),
                historyEntry("same-content-fingerprint", "incoming")
        );
        MessageHistoryMerger.Cursor repeatedCursor = MessageHistoryMerger.merge(
                "conversation-repeat", "medium_member_name", true,
                repeatedInitial, null).nextCursor;
        List<MessageHistoryMerger.Entry> repeatedCurrent = java.util.Arrays.asList(
                historyEntry("anchor", "outgoing"),
                historyEntry("same-content-fingerprint", "incoming"),
                historyEntry("same-content-fingerprint", "incoming")
        );
        MessageHistoryMerger.MergeResult repeated = MessageHistoryMerger.merge(
                "conversation-repeat", "medium_member_name", true,
                repeatedCurrent, repeatedCursor);
        require(repeated.newCandidateCount == 1
                        && repeated.annotations.get(2).newCandidate,
                "a repeated same-text fingerprint must remain a distinct ordered occurrence");

        List<MessageHistoryMerger.Entry> latestWindow = java.util.Arrays.asList(
                historyEntry("b", "incoming"),
                historyEntry("c", "outgoing"),
                historyEntry("d", "incoming")
        );
        MessageHistoryMerger.Cursor latestCursor = MessageHistoryMerger.merge(
                "conversation-scroll", "medium_member_name", true,
                latestWindow, null).nextCursor;
        List<MessageHistoryMerger.Entry> olderWindow = java.util.Arrays.asList(
                historyEntry("a", "incoming"),
                historyEntry("b", "incoming"),
                historyEntry("c", "outgoing")
        );
        MessageHistoryMerger.MergeResult historical = MessageHistoryMerger.merge(
                "conversation-scroll", "medium_member_name", true,
                olderWindow, latestCursor);
        require("historical_extension_merged".equals(historical.status)
                        && historical.newCandidateCount == 0
                        && historical.nextCursor.entries.size() == 4,
                "an older scroll overlap must prepend history without generating new events");

        List<MessageHistoryMerger.Entry> conflictPrevious = java.util.Arrays.asList(
                historyEntry("collision", "incoming")
        );
        MessageHistoryMerger.Cursor conflictCursor = MessageHistoryMerger.merge(
                "conversation-conflict", "weak_display_name", true,
                conflictPrevious, null).nextCursor;
        List<MessageHistoryMerger.Entry> conflictCurrent = java.util.Arrays.asList(
                historyEntry("collision", "outgoing")
        );
        MessageHistoryMerger.MergeResult conflict = MessageHistoryMerger.merge(
                "conversation-conflict", "weak_display_name", true,
                conflictCurrent, conflictCursor);
        require("fingerprint_conflict_uncertain".equals(conflict.status)
                        && conflict.uncertainCount == 1
                        && conflict.nextCursor.entries.size() == 2
                        && noNewCandidates(conflict),
                "a fingerprint conflict must retain both sides and suppress deletion/new claims");

        MessageHistoryMerger.MergeResult switched = MessageHistoryMerger.merge(
                "conversation-b", "weak_display_name", true,
                java.util.Arrays.asList(historyEntry("new-chat", "incoming")),
                baseline.nextCursor);
        require("conversation_changed_baseline".equals(switched.status)
                        && switched.newCandidateCount == 0,
                "a conversation identity change must establish a new baseline, not cross-merge");

        MessageHistoryMerger.MergeResult truncated = MessageHistoryMerger.merge(
                "conversation-a", "medium_member_name", false,
                withNewIncoming, baseline.nextCursor);
        require("truncated_snapshot_not_merged".equals(truncated.status)
                        && truncated.nextCursor == baseline.nextCursor
                        && noNewCandidates(truncated),
                "a truncated snapshot must preserve the prior cursor and emit no candidate");

        MessageHistoryMerger.Cursor ambiguousCursor = MessageHistoryMerger.merge(
                "conversation-ambiguous", "weak_display_name", true,
                java.util.Arrays.asList(historyEntry("same", "incoming")), null).nextCursor;
        MessageHistoryMerger.MergeResult ambiguous = MessageHistoryMerger.merge(
                "conversation-ambiguous", "weak_display_name", true,
                java.util.Arrays.asList(
                        historyEntry("same", "incoming"),
                        historyEntry("same", "incoming")),
                ambiguousCursor);
        require("ambiguous_overlap_preserved".equals(ambiguous.status)
                        && ambiguous.uncertainCount == 2
                        && noNewCandidates(ambiguous),
                "an order-ambiguous repeated fingerprint must not be guessed as new");

        List<MessageHistoryMerger.Entry> oversized = new ArrayList<>();
        for (int index = 0; index < MessageHistoryMerger.MAX_CURSOR_ENTRIES + 6; index++) {
            oversized.add(historyEntry("bounded-" + index, "incoming"));
        }
        MessageHistoryMerger.MergeResult bounded = MessageHistoryMerger.merge(
                "conversation-bounded", "medium_member_name", true, oversized, null);
        require(bounded.nextCursor.entries.size() == MessageHistoryMerger.MAX_CURSOR_ENTRIES
                        && bounded.droppedCursorEntries == 6,
                "the fingerprint cursor must remain bounded");
    }

    private static void verifyNavigationAndReadbackRules() {
        XianyuNavigationSemantics.NavigationDecision uncertain =
                XianyuNavigationSemantics.evaluateIdentity(
                        true, false, "买家甲", "", "CHAT", "买家甲", "member-a");
        require("identity_uncertain_member_missing".equals(uncertain.status)
                        && uncertain.displayNameMatched && !uncertain.safeForReply,
                "display-name-only navigation must stay explicitly unsafe for reply");

        XianyuNavigationSemantics.NavigationDecision sameNameConflict =
                XianyuNavigationSemantics.evaluateIdentity(
                        false, true, "同名买家", "member-a", "CHAT", "同名买家", "member-a");
        require("same_name_conflict".equals(sameNameConflict.status)
                        && !sameNameConflict.safeForReply,
                "same-name conflicts must stop before reply semantics");

        XianyuNavigationSemantics.NavigationDecision pageChanged =
                XianyuNavigationSemantics.evaluateIdentity(
                        true, false, "买家甲", "member-a", "MESSAGES", "买家甲", "member-a");
        require("page_changed_not_chat".equals(pageChanged.status),
                "navigation must reject a page that did not become CHAT");

        XianyuNavigationSemantics.NavigationDecision matched =
                XianyuNavigationSemantics.evaluateIdentity(
                        true, false, "买家甲", "member-a", "CHAT", "买家甲", "member-a");
        require("identity_matched".equals(matched.status) && matched.safeForReply,
                "exact display/member identity must enable read-back evaluation");

        List<XianyuNavigationSemantics.ReadbackMessage> readbackMessages =
                new ArrayList<>();
        readbackMessages.add(new XianyuNavigationSemantics.ReadbackMessage(
                0, "incoming", "text", "Hello", "incoming-fingerprint",
                "new_incoming_candidate", false));
        readbackMessages.add(new XianyuNavigationSemantics.ReadbackMessage(
                1, "outgoing", "text", "Hello", "outgoing-fingerprint",
                "new_non_incoming_observation", false));
        XianyuNavigationSemantics.ReadbackDecision confirmed =
                XianyuNavigationSemantics.evaluateReadback(
                        matched, "  Ｈｅｌｌｏ  ", readbackMessages);
        require(confirmed.confirmed
                        && "outgoing_exact_match".equals(confirmed.status)
                        && confirmed.matchedSnapshotOrder == 1
                        && "outgoing-fingerprint".equals(confirmed.matchedMessageFingerprint),
                "read-back must require one normalized exact outgoing text, ignoring incoming text");

        readbackMessages.add(new XianyuNavigationSemantics.ReadbackMessage(
                2, "outgoing", "text", "Hello", "second-outgoing-fingerprint",
                "new_non_incoming_observation", false));
        XianyuNavigationSemantics.ReadbackDecision multiple =
                XianyuNavigationSemantics.evaluateReadback(
                        matched, "Hello", readbackMessages);
        require(!multiple.confirmed
                        && "multiple_outgoing_exact_matches".equals(multiple.status)
                        && multiple.matchCount == 2,
                "multiple identical outgoing texts must remain ambiguous without a stronger receipt");

        List<XianyuNavigationSemantics.ReadbackMessage> preBoundary =
                new ArrayList<>();
        preBoundary.add(new XianyuNavigationSemantics.ReadbackMessage(
                0, "outgoing", "text", "Hello", "old-outgoing-fingerprint",
                "overlap_confirmed", false));
        XianyuNavigationSemantics.ReadbackDecision oldMatch =
                XianyuNavigationSemantics.evaluateReadback(matched, "Hello", preBoundary);
        require(!oldMatch.confirmed
                        && "outgoing_match_before_or_without_confirmed_boundary"
                        .equals(oldMatch.status),
                "an old matching outgoing message must not be mistaken for the post-send receipt");

        XianyuNavigationSemantics.ReadbackDecision blocked =
                XianyuNavigationSemantics.evaluateReadback(
                        uncertain, "Hello", readbackMessages);
        require(!blocked.confirmed && "identity_not_confirmed".equals(blocked.status),
                "outgoing text alone must never override uncertain conversation identity");
    }

    private static MessageHistoryMerger.Entry historyEntry(
            String fingerprint,
            String direction
    ) {
        return new MessageHistoryMerger.Entry(
                fingerprint, direction, "text", "medium_content_time_direction");
    }

    private static boolean noNewCandidates(MessageHistoryMerger.MergeResult result) {
        for (MessageHistoryMerger.Annotation annotation : result.annotations) {
            if (annotation.newCandidate) {
                return false;
            }
        }
        return true;
    }

    private static UiNode node(int index, int depth, String className, String text,
                               String description, boolean clickable, boolean editable,
                               boolean scrollable, int[] bounds) {
        return UiNode.fromValues(index, depth, className, "", text, description,
                clickable, editable, scrollable, true, bounds);
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static String encode(String value) {
        return Base64.getEncoder().encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String decode(String value) {
        return new String(Base64.getDecoder().decode(value), StandardCharsets.UTF_8);
    }
}
