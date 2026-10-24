package com.feifei.phonebridge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

final class CanonicalJsonWriter {
    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private CanonicalJsonWriter() {
    }

    static String digest(Object value) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] bytes = digest.digest(write(value).getBytes(StandardCharsets.UTF_8));
        return ProtocolEncoding.base64UrlEncode(bytes);
    }

    static String write(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Map) {
            Map<?, ?> map = (Map<?, ?>) value;
            List<String> keys = new ArrayList<>(map.size());
            for (Object key : map.keySet()) {
                if (!(key instanceof String)) {
                    throw new IllegalArgumentException("canonical_json_key_must_be_text");
                }
                keys.add((String) key);
            }
            keys.sort(CanonicalJsonWriter::compareUnicodeScalars);
            StringBuilder builder = new StringBuilder("{");
            for (int index = 0; index < keys.size(); index++) {
                if (index > 0) {
                    builder.append(',');
                }
                String key = keys.get(index);
                builder.append(quote(key)).append(':').append(write(map.get(key)));
            }
            return builder.append('}').toString();
        }
        if (value instanceof List) {
            List<?> list = (List<?>) value;
            StringBuilder builder = new StringBuilder("[");
            for (int index = 0; index < list.size(); index++) {
                if (index > 0) {
                    builder.append(',');
                }
                builder.append(write(list.get(index)));
            }
            return builder.append(']').toString();
        }
        if (value instanceof Boolean) {
            return value.toString();
        }
        if (value instanceof Byte || value instanceof Short ||
                value instanceof Integer || value instanceof Long) {
            return value.toString();
        }
        if (value instanceof Number) {
            throw new IllegalArgumentException("canonical_json_non_integer_number_not_supported");
        }
        return quote(value.toString());
    }

    static String quote(String value) {
        StringBuilder builder = new StringBuilder(value.length() + 2).append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"':
                    builder.append("\\\"");
                    break;
                case '\\':
                    builder.append("\\\\");
                    break;
                case '\b':
                    builder.append("\\b");
                    break;
                case '\f':
                    builder.append("\\f");
                    break;
                case '\n':
                    builder.append("\\n");
                    break;
                case '\r':
                    builder.append("\\r");
                    break;
                case '\t':
                    builder.append("\\t");
                    break;
                default:
                    if (character <= 0x1f) {
                        builder.append("\\u00")
                                .append(HEX[(character >>> 4) & 0x0f])
                                .append(HEX[character & 0x0f]);
                    } else if (Character.isHighSurrogate(character)) {
                        if (index + 1 >= value.length() || !Character.isLowSurrogate(value.charAt(index + 1))) {
                            throw new IllegalArgumentException("canonical_json_invalid_unicode");
                        }
                        builder.append(character).append(value.charAt(++index));
                    } else if (Character.isLowSurrogate(character)) {
                        throw new IllegalArgumentException("canonical_json_invalid_unicode");
                    } else {
                        builder.append(character);
                    }
            }
        }
        return builder.append('"').toString();
    }

    private static int compareUnicodeScalars(String left, String right) {
        int leftIndex = 0;
        int rightIndex = 0;
        while (leftIndex < left.length() && rightIndex < right.length()) {
            int leftPoint = left.codePointAt(leftIndex);
            int rightPoint = right.codePointAt(rightIndex);
            if (leftPoint != rightPoint) {
                return Integer.compare(leftPoint, rightPoint);
            }
            leftIndex += Character.charCount(leftPoint);
            rightIndex += Character.charCount(rightPoint);
        }
        return Integer.compare(left.length() - leftIndex, right.length() - rightIndex);
    }
}
