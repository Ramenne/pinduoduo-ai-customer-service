package com.feifei.phonebridge;

import android.content.Context;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.ResponseBody;

final class CloudApiClient {
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private static final int MAX_RESPONSE_BYTES = 256 * 1024;

    private final Context context;
    private final OkHttpClient client;

    CloudApiClient(Context context) {
        this.context = context.getApplicationContext();
        this.client = new OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(20, TimeUnit.SECONDS)
                .writeTimeout(20, TimeUnit.SECONDS)
                .callTimeout(30, TimeUnit.SECONDS)
                .build();
    }

    String registerAccount(String username, String email, String password) throws Exception {
        requireConfigured();
        JSONObject request = new JSONObject().put("username", username).put("password", password);
        if (!email.isEmpty()) request.put("email", email);
        JSONObject account = post("api/v1/accounts", request, null).getJSONObject("account");
        String saved = requireText(account, "username", 48);
        requireText(account, "id", 80);
        if (!"active".equals(account.optString("status"))) throw new CloudApiException("invalid_cloud_response");
        return saved;
    }

    EnrollmentResult enroll(String identifier, String password) throws Exception {
        requireConfigured();
        EnrollmentResult recovered = recoverAcceptedRebind();
        if (recovered != null) {
            return recovered;
        }
        boolean revokedRebind = CloudState.isRevoked(context);
        MobileSession session = login(identifier, password);
        try {
            if (revokedRebind && !DurableBindingRules.sameOwner(
                    CloudState.accountName(context), session.accountName
            )) {
                throw new CloudApiException("rebind_owner_mismatch");
            }
            String publicKey = revokedRebind
                    ? DeviceIdentity.prepareReplacementPublicKey(context)
                    : DeviceIdentity.publicKeyBase64(context);
            JSONObject challengeRequest = new JSONObject().put("public_key", publicKey);
            JSONObject challenge = post(
                    "api/v1/device-enrollment-challenges",
                    challengeRequest,
                    session.token
            );
            String challengeId = requireText(challenge, "challenge_id", 80);
            String nonce = requireText(challenge, "nonce", 128);
            EnrollmentProof.Prepared proof = EnrollmentProof.prepare(
                    challengeId,
                    nonce,
                    CanonicalJson.toMap(DeviceSnapshot.create(context))
            );
            String signature = revokedRebind
                    ? DeviceIdentity.signReplacement(context, proof.message)
                    : DeviceIdentity.sign(context, proof.message);
            JSONObject registration = postRaw(
                    "api/v1/devices",
                    proof.requestBody(signature),
                    session.token
            );
            JSONObject device = registration.getJSONObject("device");
            String deviceId = requireText(device, "id", 80);
            boolean bindingPersisted = revokedRebind
                    ? CloudState.setBindingPendingIdentityActivation(
                            context,
                            session.accountName,
                            deviceId,
                            DeviceIdentity.preparedAliasId(context)
                    )
                    : CloudState.setBinding(context, session.accountName, deviceId);
            if (!bindingPersisted) {
                // The server registration is retryable/idempotent with the same key. Do not
                // announce success or start the channel until the local binding is durable.
                throw new CloudApiException("binding_persistence_failed");
            }
            if (revokedRebind) {
                if (!DeviceIdentity.activateReplacement(context)) {
                    throw new CloudApiException("credential_activation_persistence_failed");
                }
                if (!CloudState.clearPendingIdentityActivation(context)) {
                    throw new CloudApiException("identity_activation_marker_persistence_failed");
                }
            }
            // Best-effort last-known device state lets later transport authentication avoid
            // PackageManager/permission collection if that independent lane is temporarily
            // unavailable. Failure to cache does not change the accepted binding result.
            try {
                DeviceStateCache.store(context, new JSONObject(proof.canonicalPayload));
            } catch (Exception ignored) {
            }
            ControlService.requestCloudReconnect(context);
            return new EnrollmentResult(session.accountName, deviceId);
        } finally {
            logoutBestEffort(session.token);
        }
    }

    CredentialRepairResult repairCredential(String identifier, String password)
            throws Exception {
        requireConfigured();
        String deviceId = CloudState.deviceId(context);
        if (deviceId.isEmpty()) {
            throw new CloudApiException("device_unbound");
        }
        if (CloudState.isRevoked(context)) {
            throw new CloudApiException("revoked_device_cannot_rotate_credential");
        }
        MobileSession session = login(identifier, password);
        try {
            if (!DurableBindingRules.sameOwner(
                    CloudState.accountName(context), session.accountName
            )) {
                throw new CloudApiException("credential_repair_owner_mismatch");
            }
            String publicKey = DeviceIdentity.prepareReplacementPublicKey(context);
            JSONObject requestResponse = post(
                    "api/v1/devices/" + deviceId + "/credential-repair-requests",
                    new JSONObject().put("public_key", publicKey),
                    session.token
            );
            JSONObject request = requestResponse.getJSONObject("repair_request");
            String requestId = requireText(request, "id", 80);
            JSONObject polled = get(
                    "api/v1/devices/" + deviceId + "/credential-repair-requests/" +
                            requestId,
                    session.token
            ).getJSONObject("repair_request");
            String state = requireText(polled, "state", 48);
            if ("pending_controller_approval".equals(state)) {
                return new CredentialRepairResult(state, requestId);
            }
            if ("completed".equals(state)) {
                if (!DeviceIdentity.activateReplacement(context)) {
                    throw new CloudApiException("credential_activation_persistence_failed");
                }
                ControlService.requestCloudReconnect(context);
                return new CredentialRepairResult("rotated", requestId);
            }
            if (!"approved".equals(state)) {
                throw new CloudApiException("credential_repair_state_invalid");
            }
            String challengeId = requireText(polled, "challenge_id", 80);
            String nonce = requireText(polled, "nonce", 128);
            CredentialRepairProof.Prepared proof = CredentialRepairProof.prepare(
                    deviceId,
                    challengeId,
                    nonce,
                    CanonicalJson.toMap(DeviceSnapshot.create(context))
            );
            String signature = DeviceIdentity.signReplacement(context, proof.message);
            postRaw(
                    "api/v1/devices/" + deviceId + "/credential-repair",
                    proof.requestBody(signature),
                    session.token
            );
            if (!DeviceIdentity.activateReplacement(context)) {
                throw new CloudApiException("credential_activation_persistence_failed");
            }
            ControlService.requestCloudReconnect(context);
            return new CredentialRepairResult("rotated", requestId);
        } finally {
            logoutBestEffort(session.token);
        }
    }

    private EnrollmentResult recoverAcceptedRebind() throws Exception {
        if (!CloudState.hasPendingIdentityActivation(context)) {
            return null;
        }
        String aliasId = CloudState.pendingIdentityActivationAlias(context);
        if (!DeviceIdentity.recoverPreparedActivation(context, aliasId)) {
            throw new CloudApiException("credential_activation_persistence_failed");
        }
        if (!CloudState.clearPendingIdentityActivation(context)) {
            throw new CloudApiException("identity_activation_marker_persistence_failed");
        }
        String accountName = CloudState.accountName(context);
        String deviceId = CloudState.deviceId(context);
        if (accountName.isEmpty() || deviceId.isEmpty()) {
            throw new CloudApiException("pending_rebind_binding_missing");
        }
        ControlService.requestCloudReconnect(context);
        return new EnrollmentResult(accountName, deviceId);
    }

    private MobileSession login(String identifier, String password) throws Exception {
        if (identifier == null || identifier.trim().isEmpty() || identifier.length() > 254 ||
                password == null || password.length() > 128) {
            throw new CloudApiException("invalid_login_input");
        }
        JSONObject body = new JSONObject()
                .put("identifier", identifier.trim())
                .put("password", password);
        JSONObject response = post("api/v1/mobile/sessions", body, null);
        String token = requireText(response, "access_token", 512);
        JSONObject account = response.getJSONObject("account");
        String accountName = requireText(account, "username", 80);
        return new MobileSession(token, accountName);
    }

    private JSONObject post(String path, JSONObject body, String bearer) throws Exception {
        return postRaw(path, body.toString(), bearer);
    }

    private JSONObject postRaw(String path, String body, String bearer) throws Exception {
        Request.Builder builder = new Request.Builder()
                .url(CloudConfig.endpoint(path))
                .post(RequestBody.create(body, JSON));
        if (bearer != null) {
            builder.header("Authorization", "Bearer " + bearer);
        }
        return executeJson(builder.build());
    }

    private JSONObject get(String path, String bearer) throws Exception {
        Request.Builder builder = new Request.Builder().url(CloudConfig.endpoint(path)).get();
        if (bearer != null) {
            builder.header("Authorization", "Bearer " + bearer);
        }
        return executeJson(builder.build());
    }

    private JSONObject executeJson(Request request) throws Exception {
        try (Response response = client.newCall(request).execute()) {
            byte[] bytes = readBounded(response.body());
            JSONObject json = bytes.length == 0
                    ? new JSONObject()
                    : new JSONObject(new String(bytes, StandardCharsets.UTF_8));
            if (!response.isSuccessful()) {
                String code = "cloud_http_" + response.code();
                JSONObject error = json.optJSONObject("error");
                if (error != null) {
                    String supplied = error.optString("code", "");
                    if (supplied.matches("[A-Za-z0-9_.-]{1,80}")) {
                        code = supplied;
                    }
                }
                throw new CloudApiException(code);
            }
            return json;
        } catch (IOException network) {
            throw new CloudApiException("cloud_network_error");
        }
    }

    private static byte[] readBounded(ResponseBody body) throws Exception {
        if (body == null) {
            return new byte[0];
        }
        long length = body.contentLength();
        if (length > MAX_RESPONSE_BYTES) {
            throw new CloudApiException("cloud_response_too_large");
        }
        try (InputStream input = body.byteStream();
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int total = 0;
            int read;
            while ((read = input.read(buffer)) != -1) {
                total += read;
                if (total > MAX_RESPONSE_BYTES) {
                    throw new CloudApiException("cloud_response_too_large");
                }
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        }
    }

    private void logoutBestEffort(String token) {
        if (token == null || token.isEmpty()) {
            return;
        }
        Request request = new Request.Builder()
                .url(CloudConfig.endpoint("api/v1/mobile/sessions/current"))
                .header("Authorization", "Bearer " + token)
                .delete()
                .build();
        try (Response ignored = client.newCall(request).execute()) {
            // The enrollment/revoke outcome does not depend on best-effort short-session cleanup.
        } catch (IOException ignored) {
        }
    }

    private static String requireText(JSONObject object, String key, int maximum) throws Exception {
        String value = object.optString(key, "").trim();
        if (value.isEmpty() || value.length() > maximum) {
            throw new CloudApiException("invalid_cloud_response");
        }
        return value;
    }

    private static void requireConfigured() throws CloudApiException {
        if (!CloudConfig.isConfigured()) {
            throw new CloudApiException("cloud_base_url_not_configured");
        }
    }

    static final class EnrollmentResult {
        final String accountName;
        final String deviceId;

        EnrollmentResult(String accountName, String deviceId) {
            this.accountName = accountName;
            this.deviceId = deviceId;
        }
    }

    static final class CredentialRepairResult {
        final String state;
        final String requestId;

        CredentialRepairResult(String state, String requestId) {
            this.state = state;
            this.requestId = requestId;
        }
    }

    static final class CloudApiException extends Exception {
        final String code;

        CloudApiException(String code) {
            super(code);
            this.code = code;
        }
    }

    private static final class MobileSession {
        final String token;
        final String accountName;

        MobileSession(String token, String accountName) {
            this.token = token;
            this.accountName = accountName;
        }
    }
}
