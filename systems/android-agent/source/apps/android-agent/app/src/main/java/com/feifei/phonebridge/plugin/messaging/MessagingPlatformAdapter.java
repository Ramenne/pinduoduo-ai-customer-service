package com.feifei.phonebridge.plugin.messaging;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * Mobile runtime adapter for one messaging platform.
 *
 * The cloud/task transport depends only on the stable snapshot envelope. UI-tree
 * rules remain inside the concrete adapter, so adding another Android platform
 * does not copy the cloud channel or leak its selectors into shared code.
 */
public interface MessagingPlatformAdapter {
    String platformId();

    String adapterId();

    String adapterVersion();

    String runtimeKind();

    String targetAppId();

    JSONObject buildSnapshot(JSONObject observation) throws JSONException;

    JSONObject describe() throws JSONException;
}
