package com.feifei.phonebridge;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

final class CanonicalJson {
    private CanonicalJson() {
    }

    static String enrollmentMessage(String challengeId, String nonce, JSONObject payload) throws Exception {
        return ProtocolMessages.enrollment(challengeId, nonce, toMap(payload));
    }

    static String enrollmentMessage(
            String challengeId,
            String nonce,
            Map<String, Object> payload
    ) throws Exception {
        return ProtocolMessages.enrollment(challengeId, nonce, payload);
    }

    static String websocketAuthMessage(
            String deviceId,
            String challengeId,
            String nonce,
            long issuedAt,
            JSONObject payload
    ) throws Exception {
        return ProtocolMessages.websocketAuth(
                deviceId, challengeId, nonce, issuedAt, toMap(payload)
        );
    }

    static String digest(Object value) throws Exception {
        return CanonicalJsonWriter.digest(toJava(value));
    }

    static String write(Object value) throws JSONException {
        return CanonicalJsonWriter.write(toJava(value));
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> toMap(JSONObject value) throws JSONException {
        return (Map<String, Object>) toJava(value);
    }

    private static Object toJava(Object value) throws JSONException {
        if (value == null || value == JSONObject.NULL) {
            return null;
        }
        if (value instanceof JSONObject) {
            JSONObject object = (JSONObject) value;
            Map<String, Object> result = new TreeMap<>();
            Iterator<String> iterator = object.keys();
            while (iterator.hasNext()) {
                String key = iterator.next();
                result.put(key, toJava(object.get(key)));
            }
            return result;
        }
        if (value instanceof JSONArray) {
            JSONArray array = (JSONArray) value;
            List<Object> result = new ArrayList<>(array.length());
            for (int index = 0; index < array.length(); index++) {
                result.add(toJava(array.get(index)));
            }
            return result;
        }
        return value;
    }
}
