package com.feifei.phonebridge.plugin.messaging;

import org.json.JSONException;
import org.json.JSONObject;

/** Adds the platform-neutral metadata required by oin.messaging.snapshot.v1. */
public final class MessagingSnapshotContract {
    public static final String NAME = "oin.messaging.snapshot";
    public static final int VERSION = 1;

    private MessagingSnapshotContract() {
    }

    static JSONObject decorate(MessagingPlatformAdapter adapter, JSONObject payload)
            throws JSONException {
        if (adapter == null || payload == null) {
            throw new IllegalArgumentException("adapter_and_payload_required");
        }
        JSONObject contract = new JSONObject();
        contract.put("name", NAME);
        contract.put("version", VERSION);
        contract.put("platform", adapter.platformId());
        contract.put("adapter_id", adapter.adapterId());
        contract.put("adapter_version", adapter.adapterVersion());
        contract.put("runtime", adapter.runtimeKind());
        contract.put("target_app", adapter.targetAppId());
        payload.put("contract", contract);
        return payload;
    }

    static JSONObject decorateDescription(
            MessagingPlatformAdapter adapter,
            JSONObject description
    ) throws JSONException {
        return decorate(adapter, description);
    }
}
