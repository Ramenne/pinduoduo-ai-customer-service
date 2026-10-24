package com.feifei.phonebridge;

import java.util.Base64;

final class ProtocolEncoding {
    private ProtocolEncoding() {
    }

    static String base64UrlEncode(byte[] value) {
        if (value == null) {
            throw new IllegalArgumentException("base64url_value_required");
        }
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value);
    }

    static byte[] base64UrlDecodeStrict(String value, int maximumBytes) {
        if (value == null || maximumBytes < 0 || value.length() > maximumBytes * 2 ||
                value.length() % 4 == 1) {
            throw new IllegalArgumentException("invalid_base64url");
        }
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            boolean allowed = character >= 'A' && character <= 'Z' ||
                    character >= 'a' && character <= 'z' ||
                    character >= '0' && character <= '9' ||
                    character == '-' || character == '_';
            if (!allowed) {
                throw new IllegalArgumentException("invalid_base64url");
            }
        }
        StringBuilder padded = new StringBuilder(value);
        while (padded.length() % 4 != 0) {
            padded.append('=');
        }
        final byte[] decoded;
        try {
            decoded = Base64.getUrlDecoder().decode(padded.toString());
        } catch (IllegalArgumentException invalid) {
            throw new IllegalArgumentException("invalid_base64url", invalid);
        }
        if (decoded.length > maximumBytes) {
            throw new IllegalArgumentException("decoded_value_too_large");
        }
        if (!base64UrlEncode(decoded).equals(value)) {
            throw new IllegalArgumentException("invalid_base64url");
        }
        return decoded;
    }
}
