package com.feifei.phonebridge;

import java.net.URI;

final class CloudConfig {
    private static final String PLACEHOLDER_HOST = "example.invalid";
    private static final String BASE_URL = normalize(BuildConfig.OIN_CLOUD_BASE_URL);

    private CloudConfig() {
    }

    static String endpoint(String path) {
        if (!isAllowedUrl()) {
            throw new IllegalStateException("cloud_base_url_not_allowed");
        }
        return BASE_URL + "/" + stripLeadingSlash(path);
    }

    static String websocketEndpoint() {
        String endpoint = endpoint("api/v1/device-channel");
        if (endpoint.startsWith("https://")) {
            return "wss://" + endpoint.substring("https://".length());
        }
        throw new IllegalStateException("cloud_websocket_url_not_allowed");
    }

    static boolean isTlsUrl() {
        try {
            URI uri = URI.create(BASE_URL);
            return "https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null &&
                    uri.getUserInfo() == null && uri.getFragment() == null && uri.getQuery() == null;
        } catch (RuntimeException invalid) {
            return false;
        }
    }

    static boolean isConfigured() {
        if (!isAllowedUrl()) {
            return false;
        }
        try {
            return !PLACEHOLDER_HOST.equalsIgnoreCase(URI.create(BASE_URL).getHost());
        } catch (RuntimeException invalid) {
            return false;
        }
    }

    static String transportLabel() {
        return "TLS/WSS";
    }

    static String transportCode() {
        return "tls_wss_outbound";
    }

    private static boolean isAllowedUrl() {
        return isTlsUrl();
    }

    private static String normalize(String value) {
        String normalized = value == null ? "" : value.trim();
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }

    private static String stripLeadingSlash(String value) {
        String result = value == null ? "" : value;
        while (result.startsWith("/")) {
            result = result.substring(1);
        }
        return result;
    }
}
