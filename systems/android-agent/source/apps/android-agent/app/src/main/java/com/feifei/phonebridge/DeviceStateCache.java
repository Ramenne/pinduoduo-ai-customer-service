package com.feifei.phonebridge;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

/** App-private last-known normalized device payload; never contains task/chat result data. */
final class DeviceStateCache {
    private static final String PREFS = "oin_device_state_cache";
    private static final String PAYLOAD = "canonical_payload";
    private static final String DEVICE_SCOPE = "device_scope";
    private static final int MAX_PAYLOAD_CHARS = 128 * 1024;

    private DeviceStateCache() {
    }

    static boolean store(Context context, JSONObject canonicalPayload) {
        String encoded = canonicalPayload == null ? "" : canonicalPayload.toString();
        String deviceScope = CloudState.deviceId(context);
        if (deviceScope.isEmpty() || encoded.isEmpty() ||
                encoded.length() > MAX_PAYLOAD_CHARS) {
            return false;
        }
        SharedPreferences preferences = context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return preferences.edit()
                .putString(DEVICE_SCOPE, deviceScope)
                .putString(PAYLOAD, encoded)
                .commit() &&
                deviceScope.equals(preferences.getString(DEVICE_SCOPE, "")) &&
                encoded.equals(preferences.getString(PAYLOAD, ""));
    }

    static JSONObject loadCanonical(Context context) throws Exception {
        SharedPreferences preferences = context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String currentScope = CloudState.deviceId(context);
        String cachedScope = preferences.getString(DEVICE_SCOPE, "");
        String encoded = preferences.getString(PAYLOAD, "");
        if (currentScope.isEmpty() || !currentScope.equals(cachedScope) ||
                encoded.isEmpty() || encoded.length() > MAX_PAYLOAD_CHARS) {
            throw new IllegalStateException("device_state_cache_unavailable");
        }
        JSONObject parsed = new JSONObject(encoded);
        return new JSONObject(
                DevicePayloadNormalizer.normalize(CanonicalJson.toMap(parsed))
        );
    }

    static void clear(Context context) {
        context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .remove(DEVICE_SCOPE)
                .remove(PAYLOAD)
                .apply();
    }
}
