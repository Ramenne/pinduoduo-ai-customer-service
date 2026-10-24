package com.feifei.phonebridge.plugin.messaging;

import com.feifei.phonebridge.plugin.xianyu.XianYuPlugin;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Registry of real, installed messaging adapters. No placeholder adapters are registered. */
public final class MessagingPlatformRegistry {
    private static final Map<String, MessagingPlatformAdapter> BY_PLATFORM;
    private static final Map<String, MessagingPlatformAdapter> BY_TARGET_APP;

    static {
        Map<String, MessagingPlatformAdapter> platforms = new LinkedHashMap<>();
        Map<String, MessagingPlatformAdapter> targets = new LinkedHashMap<>();
        register(platforms, targets, XianYuPlugin.INSTANCE);
        BY_PLATFORM = Collections.unmodifiableMap(platforms);
        BY_TARGET_APP = Collections.unmodifiableMap(targets);
    }

    private MessagingPlatformRegistry() {
    }

    private static void register(
            Map<String, MessagingPlatformAdapter> platforms,
            Map<String, MessagingPlatformAdapter> targets,
            MessagingPlatformAdapter adapter
    ) {
        if (adapter == null
                || adapter.platformId() == null || adapter.platformId().trim().isEmpty()
                || adapter.targetAppId() == null || adapter.targetAppId().trim().isEmpty()) {
            throw new IllegalStateException("invalid_messaging_platform_adapter");
        }
        if (platforms.put(adapter.platformId(), adapter) != null) {
            throw new IllegalStateException("duplicate_messaging_platform_id");
        }
        if (targets.put(adapter.targetAppId(), adapter) != null) {
            throw new IllegalStateException("duplicate_messaging_target_app");
        }
    }

    public static MessagingPlatformAdapter findByPlatform(String platformId) {
        return platformId == null ? null : BY_PLATFORM.get(platformId.trim());
    }

    public static MessagingPlatformAdapter findByTargetApp(String targetAppId) {
        return targetAppId == null ? null : BY_TARGET_APP.get(targetAppId.trim());
    }

    public static Set<String> supportedPlatformIds() {
        return BY_PLATFORM.keySet();
    }

    public static JSONObject snapshot(String platformId, JSONObject observation)
            throws JSONException {
        MessagingPlatformAdapter adapter = findByPlatform(platformId);
        if (adapter == null) {
            throw new IllegalArgumentException("unsupported_messaging_platform");
        }
        return MessagingSnapshotContract.decorate(adapter, adapter.buildSnapshot(observation));
    }

    public static JSONObject describe(String platformId) throws JSONException {
        MessagingPlatformAdapter adapter = findByPlatform(platformId);
        if (adapter == null) {
            throw new IllegalArgumentException("unsupported_messaging_platform");
        }
        return MessagingSnapshotContract.decorateDescription(adapter, adapter.describe());
    }
}
