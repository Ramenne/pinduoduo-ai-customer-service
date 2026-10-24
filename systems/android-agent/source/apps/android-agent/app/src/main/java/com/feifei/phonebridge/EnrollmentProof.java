package com.feifei.phonebridge;

import java.util.Map;

final class EnrollmentProof {
    private EnrollmentProof() {
    }

    static Prepared prepare(
            String challengeId,
            String nonce,
            Map<String, Object> rawPayload
    ) throws Exception {
        Map<String, Object> normalized = DevicePayloadNormalizer.normalize(rawPayload);
        String canonicalPayload = CanonicalJsonWriter.write(normalized);
        String digest = CanonicalJsonWriter.digest(normalized);
        String message = ProtocolMessages.enrollment(challengeId, nonce, normalized);
        return new Prepared(challengeId, normalized, canonicalPayload, digest, message);
    }

    static final class Prepared {
        final String challengeId;
        final Map<String, Object> payload;
        final String canonicalPayload;
        final String digest;
        final String message;

        Prepared(
                String challengeId,
                Map<String, Object> payload,
                String canonicalPayload,
                String digest,
                String message
        ) {
            this.challengeId = challengeId;
            this.payload = payload;
            this.canonicalPayload = canonicalPayload;
            this.digest = digest;
            this.message = message;
        }

        String requestBody(String signature) {
            return "{\"challenge_id\":" + CanonicalJsonWriter.quote(challengeId) +
                    ",\"payload\":" + canonicalPayload +
                    ",\"signature\":" + CanonicalJsonWriter.quote(signature) + "}";
        }
    }
}
