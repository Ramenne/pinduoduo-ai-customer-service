package com.feifei.phonebridge;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;

final class DevicePreferences {
    private static final String PREFS = "oin_device_preferences";
    private static final String DISPLAY_NAME = "display_name";

    private DevicePreferences() {
    }

    static String displayName(Context context) {
        String stored = preferences(context).getString(DISPLAY_NAME, "");
        if (!stored.isEmpty()) {
            return stored;
        }
        String manufacturer = Build.MANUFACTURER == null ? "Android" : Build.MANUFACTURER;
        String model = Build.MODEL == null ? "device" : Build.MODEL;
        return DevicePayloadNormalizer.normalizeDisplayName(manufacturer + " " + model);
    }

    static void setDisplayName(Context context, String value) {
        String normalized = DevicePayloadNormalizer.normalizeDisplayName(value);
        if (!preferences(context).edit().putString(DISPLAY_NAME, normalized).commit()) {
            throw new IllegalStateException("device_name_save_failed");
        }
    }

    private static SharedPreferences preferences(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
