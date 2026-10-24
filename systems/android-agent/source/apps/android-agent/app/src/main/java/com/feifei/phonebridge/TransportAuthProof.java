package com.feifei.phonebridge;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** Signed code26 transport authentication that has no device-state or outbox dependency. */
final class TransportAuthProof {
    static final String CONTRACT = "code26_transport_v1";
    static final int TASK_STORE_PROTOCOL = 2;

    private TransportAuthProof() {
    }

    static Prepared prepare(
            String deviceId,
            String challengeId,
            String nonce,
            long issuedAt,
            int appVersionCode,
            int taskStoreProtocol
    ) throws Exception {
        if (appVersionCode < 26) {
            throw new IllegalArgumentException("transport_auth_requires_code26");
        }
        if (taskStoreProtocol != TASK_STORE_PROTOCOL) {
            throw new IllegalArgumentException("unsupported_task_store_protocol");
        }
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("contract", CONTRACT);
        fields.put("app_version_code", appVersionCode);
        fields.put("task_store_protocol", taskStoreProtocol);
        Map<String, Object> transportAuth = Collections.unmodifiableMap(fields);
        String canonicalTransportAuth = CanonicalJsonWriter.write(transportAuth);
        String message = ProtocolMessages.websocketTransportAuth(
                deviceId,
                challengeId,
                nonce,
                issuedAt,
                transportAuth
        );
        return new Prepared(
                deviceId,
                challengeId,
                transportAuth,
                canonicalTransportAuth,
                message
        );
    }

    static final class Prepared {
        final String deviceId;
        final String challengeId;
        final Map<String, Object> transportAuth;
        final String canonicalTransportAuth;
        final String message;

        Prepared(
                String deviceId,
                String challengeId,
                Map<String, Object> transportAuth,
                String canonicalTransportAuth,
                String message
        ) {
            this.deviceId = deviceId;
            this.challengeId = challengeId;
            this.transportAuth = transportAuth;
            this.canonicalTransportAuth = canonicalTransportAuth;
            this.message = message;
        }

        String requestBody(String signature) {
            return "{\"type\":\"auth\",\"protocol_version\":\"1\",\"challenge_id\":" +
                    CanonicalJsonWriter.quote(challengeId) +
                    ",\"device_id\":" + CanonicalJsonWriter.quote(deviceId) +
                    ",\"signature\":" + CanonicalJsonWriter.quote(signature) +
                    ",\"transport_auth\":" + canonicalTransportAuth + "}";
        }
    }
}
