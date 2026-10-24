package com.feifei.phonebridge;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class ProtocolContractProbe {
    private ProtocolContractProbe() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("contract_vector_path_required");
        }
        Map<String, String> fixture = load(Path.of(args[0]));
        require("oin-v1-contract-vector-1".equals(fixture.get("format")), "fixture format");

        String challengeId = text(fixture, "challenge.id_b64");
        String nonce = text(fixture, "challenge.nonce_b64");
        EnrollmentProof.Prepared proof = EnrollmentProof.prepare(
                challengeId,
                nonce,
                rawPayload(fixture)
        );
        String expectedCanonical = text(fixture, "expected.canonical_b64");
        String expectedMessage = text(fixture, "expected.message_b64");
        require(expectedCanonical.equals(proof.canonicalPayload), "canonical payload");
        require(fixture.get("expected.digest").equals(proof.digest), "payload digest");
        require(expectedMessage.equals(proof.message), "enrollment message");

        WebsocketAuthProof.Prepared wsProof = WebsocketAuthProof.prepare(
                text(fixture, "ws.device_id_b64"),
                challengeId,
                nonce,
                Long.parseLong(fixture.get("ws.issued_at")),
                rawPayload(fixture)
        );
        require(expectedCanonical.equals(wsProof.canonicalPayload), "WS canonical payload");
        require(fixture.get("expected.digest").equals(wsProof.digest), "WS payload digest");
        require(text(fixture, "expected.ws_message_b64").equals(wsProof.message), "WS auth message");

        TransportAuthProof.Prepared transportAuthProof = TransportAuthProof.prepare(
                text(fixture, "ws.device_id_b64"),
                challengeId,
                nonce,
                Long.parseLong(fixture.get("ws.issued_at")),
                26,
                2
        );

        EnrollmentProof.Prepared diagnosticProof = EnrollmentProof.prepare(
                challengeId,
                nonce,
                diagnosticPayload(fixture)
        );
        String expectedDiagnosticCanonical = text(
                fixture,
                "expected.diagnostic_canonical_b64"
        );
        String expectedDiagnosticMessage = text(
                fixture,
                "expected.diagnostic_message_b64"
        );
        require(
                expectedDiagnosticCanonical.equals(diagnosticProof.canonicalPayload),
                "diagnostic canonical payload"
        );
        require(
                fixture.get("expected.diagnostic_digest").equals(diagnosticProof.digest),
                "diagnostic payload digest"
        );
        require(
                expectedDiagnosticMessage.equals(diagnosticProof.message),
                "diagnostic enrollment message"
        );
        WebsocketAuthProof.Prepared diagnosticWsProof = WebsocketAuthProof.prepare(
                text(fixture, "ws.device_id_b64"),
                challengeId,
                nonce,
                Long.parseLong(fixture.get("ws.issued_at")),
                diagnosticPayload(fixture)
        );
        require(
                text(fixture, "expected.diagnostic_ws_message_b64")
                        .equals(diagnosticWsProof.message),
                "diagnostic WS auth message"
        );
        CredentialRepairProof.Prepared credentialRepairProof =
                CredentialRepairProof.prepare(
                        text(fixture, "ws.device_id_b64"),
                        challengeId,
                        nonce,
                        diagnosticPayload(fixture)
                );
        require(
                text(fixture, "expected.credential_repair_message_b64")
                        .equals(credentialRepairProof.message),
                "credential repair message"
        );

        String quote = CanonicalJsonWriter.quote(text(fixture, "text.quote_input"));
        require(text(fixture, "expected.quote_b64").equals(quote), "quote vector");

        Map<String, Object> integers = new LinkedHashMap<>();
        integers.put("zero", 0);
        integers.put("one", 1);
        integers.put("minus_one", -1);
        integers.put("max", Integer.MAX_VALUE);
        String integerCanonical = CanonicalJsonWriter.write(integers);
        require(
                text(fixture, "expected.integer_canonical_b64").equals(integerCanonical),
                "integer vector"
        );
        boolean nonIntegerRejected = false;
        try {
            Map<String, Object> invalidNumber = new LinkedHashMap<>();
            invalidNumber.put("float", 1.5d);
            CanonicalJsonWriter.write(invalidNumber);
        } catch (IllegalArgumentException expected) {
            nonIntegerRejected = true;
        }
        require(nonIntegerRejected, "non-integer number rejected");

        Map<String, Object> code26DiagnosticPayload = diagnosticPayload(fixture);
        code26DiagnosticPayload.put("app_version_code", 26);
        @SuppressWarnings("unchecked")
        Map<String, Object> code26Diagnostics =
                (Map<String, Object>) code26DiagnosticPayload.get("diagnostics");
        code26Diagnostics.put("transport_failure_stage", "none");
        code26Diagnostics.put("device_state_failure_stage", "none");
        code26Diagnostics.put("outbox_failure_stage", "none");
        DevicePayloadNormalizer.normalize(code26DiagnosticPayload);
        Map<String, Object> code27DiagnosticPayload = diagnosticPayload(fixture);
        code27DiagnosticPayload.put("app_version_code", 28);
        @SuppressWarnings("unchecked")
        Map<String, Object> code27Diagnostics =
                (Map<String, Object>) code27DiagnosticPayload.get("diagnostics");
        code27Diagnostics.put("transport_failure_stage", "none");
        code27Diagnostics.put("device_state_failure_stage", "none");
        code27Diagnostics.put("outbox_failure_stage", "none");
        code27Diagnostics.put("server_session_id", "unknown");
        code27Diagnostics.put("service_watchdog_lag_ms", 0L);
        DevicePayloadNormalizer.normalize(code27DiagnosticPayload);
        Map<String, Object> code29DiagnosticPayload = diagnosticPayload(fixture);
        code29DiagnosticPayload.put("app_version_code", 29);
        @SuppressWarnings("unchecked")
        Map<String, Object> code29Diagnostics =
                (Map<String, Object>) code29DiagnosticPayload.get("diagnostics");
        code29Diagnostics.putAll(code27Diagnostics);
        code29Diagnostics.put("max_supervisor_lag_ms", 0L);
        code29Diagnostics.put("max_service_watchdog_lag_ms", 0L);
        code29Diagnostics.put("last_recovery_started_at_ms", 0L);
        code29Diagnostics.put("last_recovery_completed_at_ms", 0L);
        code29Diagnostics.put("last_recovery_attempts", 0);
        code29Diagnostics.put("last_recovery_trigger", "never");
        code29Diagnostics.put("last_recovery_failure_stage", "none");
        code29Diagnostics.put("last_recovery_outcome", "never");
        DevicePayloadNormalizer.normalize(code29DiagnosticPayload);
        boolean code29OldRejected = rejectsDiagnosticsVersion(
                code27DiagnosticPayload, 29);
        boolean code28NewRejected = rejectsDiagnosticsVersion(
                code29DiagnosticPayload, 28);
        require(code29OldRejected, "code29 old diagnostics rejected");
        require(code28NewRejected, "code28 new diagnostics rejected");
        boolean mixedCode26DiagnosticsRejected = false;
        try {
            Map<String, Object> mixed = diagnosticPayload(fixture);
            @SuppressWarnings("unchecked")
            Map<String, Object> mixedDiagnostics =
                    (Map<String, Object>) mixed.get("diagnostics");
            mixedDiagnostics.put("transport_failure_stage", "none");
            DevicePayloadNormalizer.normalize(mixed);
        } catch (IllegalArgumentException expected) {
            mixedCode26DiagnosticsRejected = true;
        }
        require(mixedCode26DiagnosticsRejected, "mixed code26 diagnostics rejected");

        byte[] privateDer = ProtocolEncoding.base64UrlDecodeStrict(
                fixture.get("ecdsa.private_pkcs8_b64"), 512
        );
        byte[] publicDer = ProtocolEncoding.base64UrlDecodeStrict(
                fixture.get("ecdsa.public_spki_b64"), 512
        );
        byte[] fixtureSignature = ProtocolEncoding.base64UrlDecodeStrict(
                fixture.get("ecdsa.signature_der_b64"), 256
        );
        KeyFactory keyFactory = KeyFactory.getInstance("EC");
        PrivateKey privateKey = keyFactory.generatePrivate(new PKCS8EncodedKeySpec(privateDer));
        PublicKey publicKey = keyFactory.generatePublic(new X509EncodedKeySpec(publicDer));

        Signature verifier = Signature.getInstance("SHA256withECDSA");
        verifier.initVerify(publicKey);
        verifier.update(proof.message.getBytes(StandardCharsets.UTF_8));
        require(verifier.verify(fixtureSignature), "fixture DER signature");

        Signature diagnosticVerifier = Signature.getInstance("SHA256withECDSA");
        diagnosticVerifier.initVerify(publicKey);
        diagnosticVerifier.update(diagnosticProof.message.getBytes(StandardCharsets.UTF_8));
        require(
                diagnosticVerifier.verify(ProtocolEncoding.base64UrlDecodeStrict(
                        fixture.get("ecdsa.diagnostic_signature_der_b64"),
                        256
                )),
                "diagnostic fixture DER signature"
        );

        Signature signer = Signature.getInstance("SHA256withECDSA");
        signer.initSign(privateKey);
        signer.update(proof.message.getBytes(StandardCharsets.UTF_8));
        byte[] javaSignature = signer.sign();

        Signature wsSigner = Signature.getInstance("SHA256withECDSA");
        wsSigner.initSign(privateKey);
        wsSigner.update(wsProof.message.getBytes(StandardCharsets.UTF_8));
        byte[] javaWsSignature = wsSigner.sign();

        Signature transportAuthSigner = Signature.getInstance("SHA256withECDSA");
        transportAuthSigner.initSign(privateKey);
        transportAuthSigner.update(
                transportAuthProof.message.getBytes(StandardCharsets.UTF_8)
        );
        byte[] javaTransportAuthSignature = transportAuthSigner.sign();

        Signature diagnosticSigner = Signature.getInstance("SHA256withECDSA");
        diagnosticSigner.initSign(privateKey);
        diagnosticSigner.update(diagnosticProof.message.getBytes(StandardCharsets.UTF_8));
        byte[] javaDiagnosticSignature = diagnosticSigner.sign();

        Signature diagnosticWsSigner = Signature.getInstance("SHA256withECDSA");
        diagnosticWsSigner.initSign(privateKey);
        diagnosticWsSigner.update(diagnosticWsProof.message.getBytes(StandardCharsets.UTF_8));
        byte[] javaDiagnosticWsSignature = diagnosticWsSigner.sign();

        Signature credentialRepairSigner = Signature.getInstance("SHA256withECDSA");
        credentialRepairSigner.initSign(privateKey);
        credentialRepairSigner.update(
                credentialRepairProof.message.getBytes(StandardCharsets.UTF_8)
        );
        byte[] javaCredentialRepairSignature = credentialRepairSigner.sign();

        boolean rawSignatureRejected;
        try {
            Signature rawVerifier = Signature.getInstance("SHA256withECDSA");
            rawVerifier.initVerify(publicKey);
            rawVerifier.update(proof.message.getBytes(StandardCharsets.UTF_8));
            rawSignatureRejected = !rawVerifier.verify(new byte[64]);
        } catch (Exception expected) {
            rawSignatureRejected = true;
        }
        require(rawSignatureRejected, "raw r||s signature rejected");

        boolean invalidBase64Rejected = false;
        try {
            ProtocolEncoding.base64UrlDecodeStrict("AAEC_w==", 16);
        } catch (IllegalArgumentException expected) {
            invalidBase64Rejected = true;
        }
        require(invalidBase64Rejected, "padded base64url rejected");

        String request = proof.requestBody(fixture.get("ecdsa.signature_der_b64"));
        require(
                request.contains("\"payload\":" + proof.canonicalPayload + ",\"signature\""),
                "signed payload equals wire payload"
        );
        String wsRequest = wsProof.requestBody(ProtocolEncoding.base64UrlEncode(javaWsSignature));
        require(
                wsRequest.endsWith("\"payload\":" + wsProof.canonicalPayload + "}"),
                "signed WS payload equals wire payload"
        );
        String transportAuthRequest = transportAuthProof.requestBody(
                ProtocolEncoding.base64UrlEncode(javaTransportAuthSignature)
        );
        require(
                transportAuthRequest.endsWith(
                        "\"transport_auth\":" +
                                transportAuthProof.canonicalTransportAuth + "}"
                ),
                "signed transport auth equals wire transport auth"
        );
        require(
                !transportAuthRequest.contains("\"payload\":"),
                "transport auth has no device payload"
        );

        output("canonical_b64", ProtocolEncoding.base64UrlEncode(
                proof.canonicalPayload.getBytes(StandardCharsets.UTF_8)
        ));
        output("digest", proof.digest);
        output("message_b64", ProtocolEncoding.base64UrlEncode(
                proof.message.getBytes(StandardCharsets.UTF_8)
        ));
        output("request_b64", ProtocolEncoding.base64UrlEncode(
                request.getBytes(StandardCharsets.UTF_8)
        ));
        output("java_signature_der_b64", ProtocolEncoding.base64UrlEncode(javaSignature));
        output("ws_message_b64", ProtocolEncoding.base64UrlEncode(
                wsProof.message.getBytes(StandardCharsets.UTF_8)
        ));
        output("ws_request_b64", ProtocolEncoding.base64UrlEncode(
                wsRequest.getBytes(StandardCharsets.UTF_8)
        ));
        output("java_ws_signature_der_b64", ProtocolEncoding.base64UrlEncode(javaWsSignature));
        output("transport_auth_message_b64", ProtocolEncoding.base64UrlEncode(
                transportAuthProof.message.getBytes(StandardCharsets.UTF_8)
        ));
        output("transport_auth_request_b64", ProtocolEncoding.base64UrlEncode(
                transportAuthRequest.getBytes(StandardCharsets.UTF_8)
        ));
        output(
                "java_transport_auth_signature_der_b64",
                ProtocolEncoding.base64UrlEncode(javaTransportAuthSignature)
        );
        output("diagnostic_canonical_b64", ProtocolEncoding.base64UrlEncode(
                diagnosticProof.canonicalPayload.getBytes(StandardCharsets.UTF_8)
        ));
        output("diagnostic_digest", diagnosticProof.digest);
        output("diagnostic_message_b64", ProtocolEncoding.base64UrlEncode(
                diagnosticProof.message.getBytes(StandardCharsets.UTF_8)
        ));
        output(
                "java_diagnostic_signature_der_b64",
                ProtocolEncoding.base64UrlEncode(javaDiagnosticSignature)
        );
        output("diagnostic_ws_message_b64", ProtocolEncoding.base64UrlEncode(
                diagnosticWsProof.message.getBytes(StandardCharsets.UTF_8)
        ));
        output(
                "java_diagnostic_ws_signature_der_b64",
                ProtocolEncoding.base64UrlEncode(javaDiagnosticWsSignature)
        );
        output("credential_repair_message_b64", ProtocolEncoding.base64UrlEncode(
                credentialRepairProof.message.getBytes(StandardCharsets.UTF_8)
        ));
        output(
                "java_credential_repair_signature_der_b64",
                ProtocolEncoding.base64UrlEncode(javaCredentialRepairSignature)
        );
        output("fixture_signature_valid", "true");
        output("raw_signature_rejected", "true");
        output("invalid_base64url_rejected", "true");
        output("non_integer_number_rejected", "true");
        output("code26_diagnostics_accepted", "true");
        output("code27_diagnostics_accepted", "true");
        output("code29_diagnostics_accepted", "true");
        output("code29_old_diagnostics_rejected", "true");
        output("code28_new_diagnostics_rejected", "true");
        output("mixed_code26_diagnostics_rejected", "true");
    }

    private static Map<String, String> load(Path path) throws Exception {
        Map<String, String> values = new LinkedHashMap<>();
        for (String raw : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            String line = raw.trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            int separator = line.indexOf('=');
            if (separator <= 0 || values.put(line.substring(0, separator), line.substring(separator + 1)) != null) {
                throw new IllegalArgumentException("invalid_or_duplicate_fixture_field");
            }
        }
        return values;
    }

    @SuppressWarnings("unchecked")
    private static boolean rejectsDiagnosticsVersion(
            Map<String, Object> source,
            int appVersionCode
    ) {
        try {
            Map<String, Object> copy = new LinkedHashMap<>(source);
            copy.put("capabilities", new LinkedHashMap<>(
                    (Map<String, Object>) source.get("capabilities")));
            copy.put("diagnostics", new LinkedHashMap<>(
                    (Map<String, Object>) source.get("diagnostics")));
            copy.put("app_version_code", appVersionCode);
            DevicePayloadNormalizer.normalize(copy);
            return false;
        } catch (IllegalArgumentException expected) {
            return true;
        }
    }

    private static Map<String, Object> rawPayload(Map<String, String> fixture) {
        Map<String, Object> payload = new LinkedHashMap<>();
        for (String field : Arrays.asList(
                "display_name", "installation_id", "manufacturer", "brand", "model",
                "android_version", "app_version", "kernel_version", "timezone", "network_type"
        )) {
            payload.put(field, text(fixture, "text." + field));
        }
        payload.put("sdk", Integer.parseInt(fixture.get("int.sdk")));
        payload.put("app_version_code", Integer.parseInt(fixture.get("int.app_version_code")));

        Map<String, Object> capabilities = new LinkedHashMap<>();
        for (String field : Arrays.asList(
                "accessibility_enabled", "accessibility_connected", "notification_enabled",
                "notification_connected", "battery_optimization_ignored",
                "background_start_user_configured", "xianyu_installed", "xianyu_rules_verified",
                "screenshot_supported", "root_signal"
        )) {
            capabilities.put(field, Boolean.parseBoolean(fixture.get("bool." + field)));
        }
        capabilities.put("xianyu_version", text(fixture, "text.xianyu_version"));
        capabilities.put("xianyu_rules_version", text(fixture, "text.xianyu_rules_version"));
        capabilities.put("local_executor_version", text(fixture, "text.local_executor_version"));
        List<String> tasks = new ArrayList<>(Arrays.asList(fixture.get("tasks").split(",", -1)));
        capabilities.put("cloud_task_types", tasks);
        payload.put("capabilities", capabilities);
        return payload;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> diagnosticPayload(Map<String, String> fixture) {
        Map<String, Object> payload = rawPayload(fixture);
        payload.put("app_version_code", 25);
        Map<String, Object> capabilities =
                (Map<String, Object>) payload.get("capabilities");
        capabilities.remove("background_start_user_configured");
        capabilities.put("background_start_status", fixture.get("diag.background_start_status"));
        capabilities.put(
                "task_store_protocol",
                Integer.parseInt(fixture.get("diag.task_store_protocol"))
        );
        capabilities.put(
                "task_channel_ready",
                Boolean.parseBoolean(fixture.get("diag.task_channel_ready"))
        );
        capabilities.put("task_store_state", fixture.get("diag.task_store_state"));

        Map<String, Object> diagnostics = new LinkedHashMap<>();
        diagnostics.put("process_instance_id", fixture.get("diag.process_instance_id"));
        diagnostics.put(
                "process_started_at_ms",
                Long.parseLong(fixture.get("diag.process_started_at_ms"))
        );
        diagnostics.put("process_pid", Integer.parseInt(fixture.get("diag.process_pid")));
        diagnostics.put("service_instance_id", fixture.get("diag.service_instance_id"));
        diagnostics.put(
                "service_started_at_ms",
                Long.parseLong(fixture.get("diag.service_started_at_ms"))
        );
        diagnostics.put(
                "last_heartbeat_sent_at_ms",
                Long.parseLong(fixture.get("diag.last_heartbeat_sent_at_ms"))
        );
        diagnostics.put(
                "last_heartbeat_ack_at_ms",
                Long.parseLong(fixture.get("diag.last_heartbeat_ack_at_ms"))
        );
        diagnostics.put(
                "supervisor_lag_ms",
                Long.parseLong(fixture.get("diag.supervisor_lag_ms"))
        );
        diagnostics.put("lifecycle_state", fixture.get("diag.lifecycle_state"));
        diagnostics.put("last_retire_reason", fixture.get("diag.last_retire_reason"));
        diagnostics.put(
                "reconnect_attempt",
                Integer.parseInt(fixture.get("diag.reconnect_attempt"))
        );
        diagnostics.put(
                "reconnect_deadline_at_ms",
                Long.parseLong(fixture.get("diag.reconnect_deadline_at_ms"))
        );
        diagnostics.put(
                "socket_epoch",
                Long.parseLong(fixture.get("diag.socket_epoch"))
        );
        payload.put("diagnostics", diagnostics);
        return payload;
    }

    private static String text(Map<String, String> fixture, String key) {
        byte[] value = ProtocolEncoding.base64UrlDecodeStrict(fixture.get(key), 4096);
        return new String(value, StandardCharsets.UTF_8);
    }

    private static void output(String key, String value) {
        System.out.println(key + "=" + value);
    }

    private static void require(boolean condition, String label) {
        if (!condition) {
            throw new AssertionError(label);
        }
    }
}
