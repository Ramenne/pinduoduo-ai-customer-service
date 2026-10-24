package com.feifei.phonebridge;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

public final class EnrollmentProtocolSelfTest {
    private static final String EXPECTED_CANONICAL =
            "{\"android_version\":\"14\",\"app_version\":\"0.3.2-v01-dev\",\"app_version_code\":6," +
            "\"brand\":\"品牌\",\"capabilities\":{\"accessibility_connected\":false," +
            "\"accessibility_enabled\":false,\"background_start_user_configured\":false," +
            "\"battery_optimization_ignored\":true,\"cloud_task_types\":[\"health\",\"observe\"," +
            "\"xianyu_snapshot\"],\"local_executor_version\":\"0.2.1-dev\"," +
            "\"notification_connected\":false,\"notification_enabled\":true,\"root_signal\":false," +
            "\"screenshot_supported\":true,\"xianyu_installed\":true," +
            "\"xianyu_rules_verified\":false,\"xianyu_rules_version\":\"0.2.1-dev\"," +
            "\"xianyu_version\":\"7.0/测试\"},\"display_name\":\"OIN 测试/机\"," +
            "\"installation_id\":\"install-001\",\"kernel_version\":\"0.2.1-dev\"," +
            "\"manufacturer\":\"Xiaomi\",\"model\":\"测试/机型\",\"network_type\":\"wifi\"," +
            "\"sdk\":35,\"timezone\":\"Asia/Shanghai\"}";
    private static final String EXPECTED_DIGEST = "DsGA3jpvn73UiB7NU6W-gFuRA4KwajPiLoGtjBZgBFY";

    private EnrollmentProtocolSelfTest() {
    }

    public static void main(String[] args) throws Exception {
        EnrollmentProof.Prepared proof = EnrollmentProof.prepare(
                "challenge-1",
                "nonce-1",
                rawPayload()
        );
        Map<String, Object> normalized = proof.payload;
        requireEquals("canonical", EXPECTED_CANONICAL, CanonicalJsonWriter.write(normalized));
        requireEquals("digest", EXPECTED_DIGEST, CanonicalJsonWriter.digest(normalized));
        requireEquals(
                "slash_unicode_control_quote",
                "\"slash/Unicode测试\\\"backslash\\\\\\b\\f\\n\\r\\t\\u0001\"",
                CanonicalJsonWriter.quote("slash/Unicode测试\"backslash\\\b\f\n\r\t\u0001")
        );
        requireEquals(
                "enrollment_message_shape",
                "oin-v1-enroll\nchallenge-1\nnonce-1\n" + EXPECTED_DIGEST,
                proof.message
        );
        String request = proof.requestBody("MEUCIQ-test-signature");
        require(
                request.contains("\"payload\":" + EXPECTED_CANONICAL + ",\"signature\""),
                "signed payload is exact wire payload"
        );
        byte[] bytes = new byte[]{0, 1, 2, (byte) 0xff};
        String encoded = ProtocolEncoding.base64UrlEncode(bytes);
        requireEquals("base64url", "AAEC_w", encoded);
        require(
                java.util.Arrays.equals(bytes, ProtocolEncoding.base64UrlDecodeStrict(encoded, 4)),
                "strict base64url round trip"
        );
        boolean paddingRejected = false;
        try {
            ProtocolEncoding.base64UrlDecodeStrict("AAEC_w==", 8);
        } catch (IllegalArgumentException expected) {
            paddingRejected = true;
        }
        require(paddingRejected, "base64url padding rejected");
        verifyActionCapabilityNormalization();
        verifyTaskStoreV2Capabilities();
        System.out.println("ENROLLMENT_PROTOCOL_SELF_TEST_PASS");
    }

    @SuppressWarnings("unchecked")
    private static void verifyActionCapabilityNormalization() {
        Map<String, Object> payload = rawPayload();
        Map<String, Object> capabilities = (Map<String, Object>) payload.get("capabilities");
        capabilities.put(
                "cloud_task_types",
                new ArrayList<>(Arrays.asList(
                        "xianyu_snapshot", "action", "health", "action", "observe"
                ))
        );
        Map<String, Object> normalized = DevicePayloadNormalizer.normalize(payload);
        Map<String, Object> normalizedCapabilities =
                (Map<String, Object>) normalized.get("capabilities");
        requireEquals(
                "action capability normalization",
                "[action, health, observe, xianyu_snapshot]",
                normalizedCapabilities.get("cloud_task_types").toString()
        );
    }

    @SuppressWarnings("unchecked")
    private static void verifyTaskStoreV2Capabilities() {
        Map<String, Object> payload = rawPayload();
        Map<String, Object> capabilities = (Map<String, Object>) payload.get("capabilities");
        capabilities.remove("background_start_user_configured");
        capabilities.put("background_start_status", "unknown");
        capabilities.put("task_store_protocol", 2);
        capabilities.put("task_channel_ready", false);
        capabilities.put("task_store_state", "recovery_pending");
        Map<String, Object> normalized = DevicePayloadNormalizer.normalize(payload);
        Map<String, Object> normalizedCapabilities =
                (Map<String, Object>) normalized.get("capabilities");
        require(Integer.valueOf(2).equals(normalizedCapabilities.get("task_store_protocol")),
                "task store protocol v2");
        require("unknown".equals(normalizedCapabilities.get("background_start_status")),
                "background start remains honestly unknown");
        require(Boolean.FALSE.equals(normalizedCapabilities.get("task_channel_ready")),
                "recovery channel fail closed");

        capabilities.put("task_channel_ready", true);
        boolean mismatchRejected = false;
        try {
            DevicePayloadNormalizer.normalize(payload);
        } catch (IllegalArgumentException expected) {
            mismatchRejected = true;
        }
        require(mismatchRejected, "ready/state mismatch rejected");
    }

    private static Map<String, Object> rawPayload() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("display_name", "  ＯＩＮ 测试/机  ");
        payload.put("installation_id", " install-001 ");
        payload.put("manufacturer", " Ｘｉａｏｍｉ ");
        payload.put("brand", "品牌");
        payload.put("model", "测试/机型");
        payload.put("android_version", " 14 ");
        payload.put("sdk", 35);
        payload.put("app_version", " 0.3.2-v01-dev ");
        payload.put("app_version_code", 6);
        payload.put("kernel_version", "0.2.1-dev");
        payload.put("timezone", " Asia/Shanghai ");
        payload.put("network_type", " wifi ");

        Map<String, Object> capabilities = new LinkedHashMap<>();
        capabilities.put("accessibility_enabled", false);
        capabilities.put("accessibility_connected", false);
        capabilities.put("notification_enabled", true);
        capabilities.put("notification_connected", false);
        capabilities.put("battery_optimization_ignored", true);
        capabilities.put("background_start_user_configured", false);
        capabilities.put("xianyu_installed", true);
        capabilities.put("xianyu_version", " 7.0/测试 ");
        capabilities.put("xianyu_rules_version", "0.2.1-dev");
        capabilities.put("xianyu_rules_verified", false);
        capabilities.put("screenshot_supported", true);
        capabilities.put("root_signal", false);
        capabilities.put("local_executor_version", "0.2.1-dev");
        capabilities.put(
                "cloud_task_types",
                new ArrayList<>(Arrays.asList("xianyu_snapshot", "health", "health", "observe"))
        );
        payload.put("capabilities", capabilities);
        return payload;
    }

    private static void requireEquals(String name, String expected, String actual) {
        if (!expected.equals(actual)) {
            throw new AssertionError(name + " mismatch\nexpected=" + expected + "\nactual=" + actual);
        }
    }

    private static void require(boolean condition, String label) {
        if (!condition) {
            throw new AssertionError(label);
        }
    }
}
