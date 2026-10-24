package com.feifei.phonebridge.plugin.xianyu;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Bounded process-local cursor state for the existing {@code xianyu_snapshot} task.
 *
 * <p>Only fingerprint/quality entries are retained. The state is intentionally not persisted:
 * Xianyu does not expose a verified strong conversation or message id, so this component reports
 * process-local de-duplication and never claims restart-safe strong uniqueness.</p>
 */
final class XianyuHistoryState {
    private static final int MAX_CONVERSATIONS = 12;

    private final LinkedHashMap<String, MessageHistoryMerger.Cursor> cursors =
            new LinkedHashMap<String, MessageHistoryMerger.Cursor>(16, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(
                        Map.Entry<String, MessageHistoryMerger.Cursor> eldest
                ) {
                    return size() > MAX_CONVERSATIONS;
                }
            };
    private String activeConversationKey = "";

    synchronized JSONObject enrich(JSONObject snapshot) throws JSONException {
        if (snapshot == null) {
            return null;
        }
        JSONObject messagePayload = snapshot.optJSONObject("messages");
        if (messagePayload == null) {
            return snapshot;
        }
        String page = messagePayload.optString("page", "");
        if (!"CHAT".equals(page)) {
            messagePayload.put("incremental_candidates", new JSONArray());
            messagePayload.put("history", historyWithoutMerge("not_chat", "", "", false));
            activeConversationKey = "";
            return snapshot;
        }

        JSONObject context = messagePayload.optJSONObject("chat_context");
        String conversationKey = context == null
                ? "" : context.optString("identity_fingerprint", "").trim();
        String identityQuality = context == null
                ? "" : context.optString("identity_quality", "").trim();
        boolean conversationChanged = !activeConversationKey.isEmpty()
                && !activeConversationKey.equals(conversationKey);
        MessageHistoryMerger.Cursor previous = conversationKey.isEmpty()
                ? null : cursors.get(conversationKey);

        JSONArray messages = messagePayload.optJSONArray("messages");
        List<MessageHistoryMerger.Entry> current = new ArrayList<>();
        if (messages != null) {
            for (int index = 0; index < messages.length(); index++) {
                JSONObject message = messages.optJSONObject(index);
                current.add(message == null
                        ? new MessageHistoryMerger.Entry("", "", "", "")
                        : new MessageHistoryMerger.Entry(
                                message.optString("message_fingerprint", ""),
                                message.optString("direction", ""),
                                message.optString("type", ""),
                                message.optString("fingerprint_quality", "")
                        ));
            }
        }

        MessageHistoryMerger.MergeResult merge = MessageHistoryMerger.merge(
                conversationKey,
                identityQuality,
                messagePayload.optBoolean("snapshot_complete", false),
                current,
                previous
        );
        if (merge.cursorUpdated && merge.nextCursor != null && !conversationKey.isEmpty()) {
            cursors.put(conversationKey, merge.nextCursor);
        }
        activeConversationKey = conversationKey;

        JSONArray newCandidates = new JSONArray();
        if (messages != null) {
            for (int index = 0; index < messages.length(); index++) {
                JSONObject message = messages.optJSONObject(index);
                if (message == null || index >= merge.annotations.size()) {
                    continue;
                }
                MessageHistoryMerger.Annotation annotation = merge.annotations.get(index);
                message.put("new_candidate", annotation.newCandidate);
                message.put("merge_state", annotation.state);
                message.put("merge_uncertain", annotation.uncertain);
                if (annotation.newCandidate) {
                    newCandidates.put(new JSONObject()
                            .put("snapshot_order", message.optInt("snapshot_order", index))
                            .put("message_fingerprint",
                                    message.optString("message_fingerprint", ""))
                            .put("direction", message.optString("direction", ""))
                            .put("type", message.optString("type", "")));
                }
            }
        }
        messagePayload.put("incremental_candidates", newCandidates);

        JSONObject history = new JSONObject();
        history.put("cursor_version", 1);
        history.put("status", merge.status);
        history.put("conversation_key", conversationKey);
        history.put("identity_quality", identityQuality);
        history.put("conversation_changed", conversationChanged);
        history.put("previous_cursor_available", previous != null);
        history.put("overlap_count", merge.overlapCount);
        history.put("new_candidate_count", merge.newCandidateCount);
        history.put("uncertain_count", merge.uncertainCount);
        history.put("cursor_entry_count", merge.nextCursor == null
                ? 0 : merge.nextCursor.entries.size());
        history.put("dropped_cursor_entries", merge.droppedCursorEntries);
        history.put("cursor_scope", "bounded_process_memory");
        history.put("cursor_contains_raw_chat_content", false);
        history.put("cross_restart_dedup_guaranteed", false);
        history.put("strong_platform_identity_claimed", false);
        history.put("note", historyNote(merge.status));
        messagePayload.put("history", history);
        return snapshot;
    }

    private static JSONObject historyWithoutMerge(
            String status,
            String conversationKey,
            String identityQuality,
            boolean conversationChanged
    ) throws JSONException {
        return new JSONObject()
                .put("cursor_version", 1)
                .put("status", status)
                .put("conversation_key", conversationKey)
                .put("identity_quality", identityQuality)
                .put("conversation_changed", conversationChanged)
                .put("previous_cursor_available", false)
                .put("overlap_count", 0)
                .put("new_candidate_count", 0)
                .put("uncertain_count", 0)
                .put("cursor_entry_count", 0)
                .put("dropped_cursor_entries", 0)
                .put("cursor_scope", "bounded_process_memory")
                .put("cursor_contains_raw_chat_content", false)
                .put("cross_restart_dedup_guaranteed", false)
                .put("strong_platform_identity_claimed", false)
                .put("note", "history merge is applicable only to a complete CHAT snapshot");
    }

    private static String historyNote(String status) {
        if ("baseline_established".equals(status)
                || "conversation_changed_baseline".equals(status)) {
            return "current visible messages establish a boundary and are not emitted as new";
        }
        if ("truncated_snapshot_not_merged".equals(status)) {
            return "truncated data is preserved but cannot advance the de-duplication cursor";
        }
        if (status.contains("uncertain") || status.contains("conflict")) {
            return "both sides are retained as fingerprint candidates; no uncertain incoming is emitted";
        }
        return "fingerprints are overlap candidates, not strong platform message identifiers";
    }
}
