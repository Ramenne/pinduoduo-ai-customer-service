package com.feifei.phonebridge;

import java.util.Map;

final class CredentialRepairProof {
    private CredentialRepairProof() {
    }

    static Prepared prepare(
            String deviceId,
            String challengeId,
            String nonce,
            Map<String, Object> rawPayload
    ) throws Exception {
        Map<String, Object> normalized = DevicePayloadNormalizer.normalize(rawPayload);
        String canonicalPayload = CanonicalJsonWriter.write(normalized);
        String message = ProtocolMessages.credentialRepair(
                deviceId, challengeId, nonce, normalized
        );
        return new Prepared(challengeId, canonicalPayload, message);
    }

    static final class Prepared {
        final String challengeId;
        final String canonicalPayload;
        final String message;

        Prepared(String challengeId, String canonicalPayload, String message) {
            this.challengeId = challengeId;
            this.canonicalPayload = canonicalPayload;
            this.message = message;
        }

        String requestBody(String signature) {
            return "{\"challenge_id\":" + CanonicalJsonWriter.quote(challengeId) +
                    ",\"payload\":" + canonicalPayload +
                    ",\"signature\":" + CanonicalJsonWriter.quote(signature) + "}";
        }
    }
}
