package com.feifei.phonebridge;

import java.util.Map;

/** Pure-Java signed-message contract shared by Android runtime and desktop probes. */
final class ProtocolMessages {
    private ProtocolMessages() {
    }

    static String enrollment(
            String challengeId,
            String nonce,
            Map<String, Object> payload
    ) throws Exception {
        return "oin-v1-enroll\n" + challengeId + "\n" + nonce + "\n" +
                CanonicalJsonWriter.digest(payload);
    }

    static String websocketAuth(
            String deviceId,
            String challengeId,
            String nonce,
            long issuedAt,
            Map<String, Object> payload
    ) throws Exception {
        return "oin-v1-wss-auth\n" + deviceId + "\n" + challengeId + "\n" + nonce +
                "\n" + issuedAt + "\n" + CanonicalJsonWriter.digest(payload);
    }

    static String websocketTransportAuth(
            String deviceId,
            String challengeId,
            String nonce,
            long issuedAt,
            Map<String, Object> transportAuth
    ) throws Exception {
        return "oin-v1-wss-transport-auth\n" + deviceId + "\n" + challengeId + "\n" +
                nonce + "\n" + issuedAt + "\n" + CanonicalJsonWriter.digest(transportAuth);
    }

    static String credentialRepair(
            String deviceId,
            String challengeId,
            String nonce,
            Map<String, Object> payload
    ) throws Exception {
        return "oin-v1-credential-repair\n" + deviceId + "\n" + challengeId +
                "\n" + nonce + "\n" + CanonicalJsonWriter.digest(payload);
    }

}
