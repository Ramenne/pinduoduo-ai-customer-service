package com.feifei.phonebridge;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;

import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import java.util.UUID;

final class DeviceIdentity {
    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String KEY_ALIAS_A = "oin_v01_device_p256";
    private static final String KEY_ALIAS_B = "oin_v01_device_p256_rotated";
    private static final String PREFS = "oin_device_identity";
    private static final String INSTALLATION_ID = "installation_id";
    private static final String ACTIVE_KEY_ALIAS = "active_key_alias";
    private static final String PREPARED_KEY_ALIAS = "prepared_key_alias";

    private DeviceIdentity() {
    }

    static synchronized String publicKeyBase64(Context context) throws DeviceIdentityException {
        try {
            KeyPair pair = ensureKeyPair(activeAlias(context));
            return ProtocolEncoding.base64UrlEncode(pair.getPublic().getEncoded());
        } catch (DeviceIdentityException expected) {
            throw expected;
        } catch (Exception failure) {
            throw new DeviceIdentityException("device_keystore_unavailable", failure);
        }
    }

    static synchronized String sign(Context context, String message) throws DeviceIdentityException {
        return signWithAlias(activeAlias(context), message);
    }

    static synchronized String prepareReplacementPublicKey(Context context)
            throws DeviceIdentityException {
        String replacementAlias = replacementAlias(context);
        try {
            SharedPreferences preferences = preferences(context);
            KeyStore store = KeyStore.getInstance(KEYSTORE);
            store.load(null);
            if (replacementAlias.equals(
                    preferences.getString(PREPARED_KEY_ALIAS, "")
            ) && store.getEntry(replacementAlias, null) instanceof KeyStore.PrivateKeyEntry) {
                return ProtocolEncoding.base64UrlEncode(
                        ((KeyStore.PrivateKeyEntry) store.getEntry(replacementAlias, null))
                                .getCertificate().getPublicKey().getEncoded()
                );
            }
            if (store.containsAlias(replacementAlias)) {
                store.deleteEntry(replacementAlias);
            }
            String publicKey = ProtocolEncoding.base64UrlEncode(
                    ensureKeyPair(replacementAlias).getPublic().getEncoded()
            );
            if (!preferences.edit().putString(PREPARED_KEY_ALIAS, replacementAlias).commit()) {
                throw new DeviceIdentityException("device_identity_state_persistence_failed");
            }
            return publicKey;
        } catch (DeviceIdentityException expected) {
            throw expected;
        } catch (Exception failure) {
            throw new DeviceIdentityException("device_keystore_unavailable", failure);
        }
    }

    static synchronized String replacementPublicKey(Context context)
            throws DeviceIdentityException {
        try {
            return ProtocolEncoding.base64UrlEncode(
                    requireKeyPair(replacementAlias(context)).getPublic().getEncoded()
            );
        } catch (DeviceIdentityException expected) {
            throw expected;
        } catch (Exception failure) {
            throw new DeviceIdentityException("device_keystore_unavailable", failure);
        }
    }

    static synchronized String signReplacement(Context context, String message)
            throws DeviceIdentityException {
        return signWithAlias(replacementAlias(context), message);
    }

    static synchronized boolean activateReplacement(Context context)
            throws DeviceIdentityException {
        String preparedAlias = preferences(context).getString(PREPARED_KEY_ALIAS, "");
        if (!isKnownAlias(preparedAlias)) {
            throw new DeviceIdentityException("replacement_key_not_prepared");
        }
        return activateAlias(context, preparedAlias);
    }

    static synchronized String preparedAliasId(Context context)
            throws DeviceIdentityException {
        String preparedAlias = preferences(context).getString(PREPARED_KEY_ALIAS, "");
        if (!isKnownAlias(preparedAlias)) {
            throw new DeviceIdentityException("replacement_key_not_prepared");
        }
        return aliasId(preparedAlias);
    }

    static synchronized boolean recoverPreparedActivation(Context context, String expectedAliasId)
            throws DeviceIdentityException {
        String expectedAlias = aliasFromId(expectedAliasId);
        SharedPreferences preferences = preferences(context);
        String preparedAlias = preferences.getString(PREPARED_KEY_ALIAS, "");
        if (expectedAlias.equals(preparedAlias)) {
            return activateAlias(context, expectedAlias);
        }
        if (preparedAlias.isEmpty() && expectedAlias.equals(activeAlias(context))) {
            try {
                requireKeyPair(expectedAlias);
                return true;
            } catch (DeviceIdentityException expected) {
                throw expected;
            } catch (Exception failure) {
                throw new DeviceIdentityException("device_keystore_unavailable", failure);
            }
        }
        throw new DeviceIdentityException("pending_identity_alias_mismatch");
    }

    private static boolean activateAlias(Context context, String replacementAlias)
            throws DeviceIdentityException {
        try {
            requireKeyPair(replacementAlias);
            SharedPreferences preferences = preferences(context);
            return preferences.edit()
                    .putString(ACTIVE_KEY_ALIAS, replacementAlias)
                    .remove(PREPARED_KEY_ALIAS)
                    .commit() &&
                    replacementAlias.equals(preferences.getString(ACTIVE_KEY_ALIAS, ""));
        } catch (DeviceIdentityException expected) {
            throw expected;
        } catch (Exception failure) {
            throw new DeviceIdentityException("device_keystore_unavailable", failure);
        }
    }

    private static boolean isKnownAlias(String alias) {
        return KEY_ALIAS_A.equals(alias) || KEY_ALIAS_B.equals(alias);
    }

    private static String aliasId(String alias) throws DeviceIdentityException {
        if (KEY_ALIAS_A.equals(alias)) {
            return "A";
        }
        if (KEY_ALIAS_B.equals(alias)) {
            return "B";
        }
        throw new DeviceIdentityException("pending_identity_alias_invalid");
    }

    private static String aliasFromId(String aliasId) throws DeviceIdentityException {
        if ("A".equals(aliasId)) {
            return KEY_ALIAS_A;
        }
        if ("B".equals(aliasId)) {
            return KEY_ALIAS_B;
        }
        throw new DeviceIdentityException("pending_identity_alias_invalid");
    }

    static synchronized boolean hasPrivateKey(Context context) {
        try {
            KeyStore store = KeyStore.getInstance(KEYSTORE);
            store.load(null);
            return store.getEntry(activeAlias(context), null) instanceof KeyStore.PrivateKeyEntry;
        } catch (Exception failure) {
            return false;
        }
    }

    static synchronized void deleteKey() throws Exception {
        KeyStore store = KeyStore.getInstance(KEYSTORE);
        store.load(null);
        if (store.containsAlias(KEY_ALIAS_A)) {
            store.deleteEntry(KEY_ALIAS_A);
        }
        if (store.containsAlias(KEY_ALIAS_B)) {
            store.deleteEntry(KEY_ALIAS_B);
        }
    }

    static String installationId(Context context) {
        SharedPreferences preferences = context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String value = preferences.getString(INSTALLATION_ID, "");
        if (!value.isEmpty()) {
            return value;
        }
        synchronized (DeviceIdentity.class) {
            value = preferences.getString(INSTALLATION_ID, "");
            if (value.isEmpty()) {
                value = UUID.randomUUID().toString();
                if (!preferences.edit().putString(INSTALLATION_ID, value).commit()) {
                    throw new IllegalStateException("installation_id_persistence_failed");
                }
            }
            return value;
        }
    }

    private static String signWithAlias(String alias, String message)
            throws DeviceIdentityException {
        try {
            PrivateKey key = requireKeyPair(alias).getPrivate();
            Signature signature = Signature.getInstance("SHA256withECDSA");
            signature.initSign(key);
            signature.update(message.getBytes(StandardCharsets.UTF_8));
            return ProtocolEncoding.base64UrlEncode(signature.sign());
        } catch (DeviceIdentityException expected) {
            throw expected;
        } catch (Exception failure) {
            throw new DeviceIdentityException("device_private_key_unusable", failure);
        }
    }

    private static KeyPair requireKeyPair(String alias) throws Exception {
        KeyStore store = KeyStore.getInstance(KEYSTORE);
        store.load(null);
        KeyStore.Entry entry = store.getEntry(alias, null);
        if (entry instanceof KeyStore.PrivateKeyEntry) {
            KeyStore.PrivateKeyEntry privateEntry = (KeyStore.PrivateKeyEntry) entry;
            return new KeyPair(privateEntry.getCertificate().getPublicKey(), privateEntry.getPrivateKey());
        }
        throw new DeviceIdentityException("device_private_key_missing");
    }

    private static KeyPair ensureKeyPair(String alias) throws Exception {
        try {
            return requireKeyPair(alias);
        } catch (DeviceIdentityException missing) {
            if (!"device_private_key_missing".equals(missing.code)) {
                throw missing;
            }
        }
        KeyPairGenerator generator = KeyPairGenerator.getInstance(
                KeyProperties.KEY_ALGORITHM_EC,
                KEYSTORE
        );
        generator.initialize(new KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_SIGN | KeyProperties.PURPOSE_VERIFY
        )
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(false)
                .build());
        return generator.generateKeyPair();
    }

    private static String activeAlias(Context context) {
        String value = preferences(context).getString(ACTIVE_KEY_ALIAS, KEY_ALIAS_A);
        return KEY_ALIAS_B.equals(value) ? KEY_ALIAS_B : KEY_ALIAS_A;
    }

    private static String replacementAlias(Context context) {
        return KEY_ALIAS_A.equals(activeAlias(context)) ? KEY_ALIAS_B : KEY_ALIAS_A;
    }

    private static SharedPreferences preferences(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static final class DeviceIdentityException extends Exception {
        final String code;

        DeviceIdentityException(String code) {
            super(code);
            this.code = code;
        }

        DeviceIdentityException(String code, Throwable cause) {
            super(code, cause);
            this.code = code;
        }
    }
}
