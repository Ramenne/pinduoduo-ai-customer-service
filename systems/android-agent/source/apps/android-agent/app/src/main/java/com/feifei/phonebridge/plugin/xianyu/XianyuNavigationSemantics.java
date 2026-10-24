package com.feifei.phonebridge.plugin.xianyu;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Pure identity and outgoing read-back decisions for Board 1.
 *
 * <p>This class never clicks, focuses, writes, or sends. It only evaluates facts already present
 * in a MESSAGES target and a later CHAT snapshot.</p>
 */
public final class XianyuNavigationSemantics {
    private XianyuNavigationSemantics() {
    }

    /**
     * JSON adapter for the later Board 2/3 caller. The expected text is compared exactly after
     * NFKC normalization and trimming, but the returned decision contains only its fingerprint.
     */
    public static JSONObject evaluate(
            JSONObject targetConversation,
            JSONObject snapshotOrMessagePayload,
            String expectedOutgoingText
    ) throws JSONException {
        JSONObject payload = messagePayload(snapshotOrMessagePayload);
        JSONObject context = payload == null ? null : payload.optJSONObject("chat_context");
        NavigationDecision navigation = evaluateIdentity(
                targetConversation != null
                        && targetConversation.optBoolean("locator_available", false),
                targetConversation != null
                        && targetConversation.optBoolean("identity_ambiguous", false),
                targetConversation == null
                        ? "" : targetConversation.optString("display_name", ""),
                targetConversation == null
                        ? "" : targetConversation.optString("member_name", ""),
                payload == null ? "" : payload.optString("page", ""),
                context == null ? "" : context.optString("display_name", ""),
                context == null ? "" : context.optString("member_name", "")
        );

        List<ReadbackMessage> messages = new ArrayList<>();
        JSONArray array = payload == null ? null : payload.optJSONArray("messages");
        if (array != null) {
            for (int index = 0; index < array.length(); index++) {
                JSONObject message = array.optJSONObject(index);
                if (message != null) {
                    messages.add(new ReadbackMessage(
                            message.optInt("snapshot_order", index),
                            message.optString("direction", ""),
                            message.optString("type", ""),
                            message.optString("text", ""),
                            message.optString("message_fingerprint", ""),
                            message.optString("merge_state", ""),
                            message.optBoolean("merge_uncertain", true)
                    ));
                }
            }
        }
        ReadbackDecision readback = evaluateReadback(
                navigation,
                expectedOutgoingText,
                messages
        );

        JSONObject out = new JSONObject();
        out.put("navigation", navigation.toJson());
        out.put("readback", readback.toJson());
        out.put("performed_input_or_send", false);
        return out;
    }

    static NavigationDecision evaluateIdentity(
            boolean locatorAvailable,
            boolean identityAmbiguous,
            String targetDisplayName,
            String targetMemberName,
            String currentPage,
            String currentDisplayName,
            String currentMemberName
    ) {
        String targetDisplay = MessageExtractor.normalize(targetDisplayName);
        String targetMember = MessageExtractor.normalize(targetMemberName);
        String currentDisplay = MessageExtractor.normalize(currentDisplayName);
        String currentMember = MessageExtractor.normalize(currentMemberName);
        if (!"CHAT".equals(safe(currentPage))) {
            return new NavigationDecision("page_changed_not_chat", false, false,
                    "CHAT page is required after navigation");
        }
        if (identityAmbiguous) {
            return new NavigationDecision("same_name_conflict", false, false,
                    "target display name is not unique in the visible conversation list");
        }
        if (!locatorAvailable) {
            return new NavigationDecision("locator_unavailable", false, false,
                    "no exact supported selector was proven for this target");
        }
        if (targetDisplay.isEmpty() || currentDisplay.isEmpty()) {
            return new NavigationDecision("display_name_missing", false, false,
                    "both list and chat display names are required");
        }
        if (!targetDisplay.equals(currentDisplay)) {
            return new NavigationDecision("display_name_mismatch", false, false,
                    "chat identity does not match the selected list row");
        }
        if (targetMember.isEmpty() || currentMember.isEmpty()) {
            return new NavigationDecision("identity_uncertain_member_missing", true, false,
                    "display name matches, but member identity is missing on one side");
        }
        if (!targetMember.equals(currentMember)) {
            return new NavigationDecision("member_name_mismatch", false, false,
                    "member identity differs from the expected target");
        }
        return new NavigationDecision("identity_matched", true, true,
                "display and member identities match exactly");
    }

    static ReadbackDecision evaluateReadback(
            NavigationDecision navigation,
            String expectedOutgoingText,
            List<ReadbackMessage> messages
    ) {
        String expected = MessageExtractor.normalize(expectedOutgoingText);
        String expectedFingerprint = expected.isEmpty()
                ? "" : MessageExtractor.stableFingerprint("readback_expected", expected);
        if (navigation == null || !navigation.safeForReply) {
            return new ReadbackDecision(
                    "identity_not_confirmed", false, 0, -1, "", expectedFingerprint);
        }
        if (expected.isEmpty()) {
            return new ReadbackDecision(
                    "expected_text_missing", false, 0, -1, "", "");
        }
        int matches = 0;
        int exactOutgoingMatches = 0;
        int matchedOrder = -1;
        String matchedFingerprint = "";
        if (messages != null) {
            for (ReadbackMessage message : messages) {
                if (message != null
                        && "outgoing".equals(message.direction)
                        && "text".equals(message.type)
                        && expected.equals(MessageExtractor.normalize(message.text))) {
                    exactOutgoingMatches++;
                    if ("new_non_incoming_observation".equals(message.mergeState)
                            && !message.mergeUncertain) {
                        matches++;
                        matchedOrder = message.snapshotOrder;
                        matchedFingerprint = message.fingerprint;
                    }
                }
            }
        }
        if (matches == 1) {
            return new ReadbackDecision(
                    "outgoing_exact_match", true, 1, matchedOrder,
                    matchedFingerprint, expectedFingerprint);
        }
        if (matches > 1) {
            return new ReadbackDecision(
                    "multiple_outgoing_exact_matches", false, matches, -1,
                    "", expectedFingerprint);
        }
        if (exactOutgoingMatches > 0) {
            return new ReadbackDecision(
                    "outgoing_match_before_or_without_confirmed_boundary", false,
                    exactOutgoingMatches, -1, "", expectedFingerprint);
        }
        return new ReadbackDecision(
                "outgoing_text_not_found", false, 0, -1, "", expectedFingerprint);
    }

    private static JSONObject messagePayload(JSONObject snapshotOrPayload) {
        if (snapshotOrPayload == null) {
            return null;
        }
        Object nested = snapshotOrPayload.opt("messages");
        return nested instanceof JSONObject ? (JSONObject) nested : snapshotOrPayload;
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }

    static final class NavigationDecision {
        final String status;
        final boolean displayNameMatched;
        final boolean safeForReply;
        final String note;

        NavigationDecision(String status, boolean displayNameMatched,
                           boolean safeForReply, String note) {
            this.status = status;
            this.displayNameMatched = displayNameMatched;
            this.safeForReply = safeForReply;
            this.note = note;
        }

        JSONObject toJson() throws JSONException {
            return new JSONObject()
                    .put("status", status)
                    .put("display_name_matched", displayNameMatched)
                    .put("safe_for_reply", safeForReply)
                    .put("note", note);
        }
    }

    static final class ReadbackMessage {
        final int snapshotOrder;
        final String direction;
        final String type;
        final String text;
        final String fingerprint;
        final String mergeState;
        final boolean mergeUncertain;

        ReadbackMessage(int snapshotOrder, String direction, String type,
                        String text, String fingerprint, String mergeState,
                        boolean mergeUncertain) {
            this.snapshotOrder = snapshotOrder;
            this.direction = safe(direction);
            this.type = safe(type);
            this.text = text == null ? "" : text;
            this.fingerprint = safe(fingerprint);
            this.mergeState = safe(mergeState);
            this.mergeUncertain = mergeUncertain;
        }
    }

    static final class ReadbackDecision {
        final String status;
        final boolean confirmed;
        final int matchCount;
        final int matchedSnapshotOrder;
        final String matchedMessageFingerprint;
        final String expectedTextFingerprint;

        ReadbackDecision(String status, boolean confirmed, int matchCount,
                         int matchedSnapshotOrder, String matchedMessageFingerprint,
                         String expectedTextFingerprint) {
            this.status = status;
            this.confirmed = confirmed;
            this.matchCount = Math.max(0, matchCount);
            this.matchedSnapshotOrder = matchedSnapshotOrder;
            this.matchedMessageFingerprint = safe(matchedMessageFingerprint);
            this.expectedTextFingerprint = safe(expectedTextFingerprint);
        }

        JSONObject toJson() throws JSONException {
            return new JSONObject()
                    .put("status", status)
                    .put("confirmed", confirmed)
                    .put("match_count", matchCount)
                    .put("matched_snapshot_order", matchedSnapshotOrder)
                    .put("matched_message_fingerprint", matchedMessageFingerprint)
                    .put("expected_text_fingerprint", expectedTextFingerprint)
                    .put("match_rule",
                            "newly_observed_after_confirmed_cursor_plus_nfkc_exact_outgoing_text");
        }
    }
}
