package com.feifei.phonebridge;

import java.util.Map;

/** Prepares one WS authentication proof so the signed payload bytes are sent unchanged. */
final class WebsocketAuthProof {
    private WebsocketAuthProof() {
    }

    static Prepared prepare(
            String deviceId,
            String challengeId,
            String nonce,
            long issuedAt,
            Map<String, Object> rawPayload
    ) throws Exception {
        Map<String, Object> normalized = DevicePayloadNormalizer.normalize(rawPayload);
        String canonicalPayload = CanonicalJsonWriter.write(normalized);
        String digest = CanonicalJsonWriter.digest(normalized);
        String message = ProtocolMessages.websocketAuth(
                deviceId, challengeId, nonce, issuedAt, normalized
        );
        return new Prepared(
                deviceId,
                challengeId,
                issuedAt,
                normalized,
                canonicalPayload,
                digest,
                message
        );
    }

    static final class Prepared {
        final String deviceId;
        final String challengeId;
        final long issuedAt;
        final Map<String, Object> payload;
        final String canonicalPayload;
        final String digest;
        final String message;

        Prepared(
                String deviceId,
                String challengeId,
                long issuedAt,
                Map<String, Object> payload,
                String canonicalPayload,
                String digest,
                String message
        ) {
            this.deviceId = deviceId;
            this.challengeId = challengeId;
            this.issuedAt = issuedAt;
            this.payload = payload;
            this.canonicalPayload = canonicalPayload;
            this.digest = digest;
            this.message = message;
        }

        String requestBody(String signature) {
            return "{\"type\":\"auth\",\"protocol_version\":\"1\",\"challenge_id\":" +
                    CanonicalJsonWriter.quote(challengeId) +
                    ",\"device_id\":" + CanonicalJsonWriter.quote(deviceId) +
                    ",\"signature\":" + CanonicalJsonWriter.quote(signature) +
                    ",\"payload\":" + canonicalPayload + "}";
        }
    }
}
