package com.feifei.phonebridge;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.Locale;

/** Byte-level admission; Android must additionally decode a real bounded JPEG/PNG. */
final class ImageImportContract {
    static final int MAX_RAW_BYTES = 262144;
    static final int MAX_BASE64_CHARS = ((MAX_RAW_BYTES + 2) / 3) * 4;
    private ImageImportContract() {}

    static String validate(Object mime, Object filename, Object sha, Object base64) {
        if (!(mime instanceof String) ||
                !("image/jpeg".equals(mime) || "image/png".equals(mime))) {
            return "invalid_image_mime_type";
        }
        if (!(sha instanceof String) || !((String) sha).matches("[a-fA-F0-9]{64}")) {
            return "invalid_image_sha256";
        }
        String expected = "oin-" + ((String) sha).toLowerCase(Locale.ROOT).substring(0, 16) +
                ("image/jpeg".equals(mime) ? ".jpg" : ".png");
        if (!expected.equals(filename)) return "invalid_image_filename";
        if (!(base64 instanceof String)) return "invalid_image_base64";
        String encoded = (String) base64;
        if (encoded.isEmpty() || encoded.length() > MAX_BASE64_CHARS) return "image_size_out_of_bounds";
        // Canonical standard padded Base64 only: no data URL, whitespace or URL alphabet.
        if (encoded.length() % 4 != 0 ||
                !encoded.matches("[A-Za-z0-9+/]*={0,2}")) return "invalid_image_base64";
        return null;
    }

    static byte[] decode(String mime, String filename, String sha, String encoded) throws IOException {
        String error = validate(mime, filename, sha, encoded);
        if (error != null) throw new IOException(error);
        byte[] bytes;
        try (InputStream input = Base64.getDecoder().wrap(new ByteArrayInputStream(
                encoded.getBytes(StandardCharsets.US_ASCII)))) {
            bytes = readBounded(input);
        } catch (IOException invalid) {
            if ("image_size_out_of_bounds".equals(invalid.getMessage())) throw invalid;
            throw new IOException("invalid_image_base64");
        }
        if (!encoded.equals(Base64.getEncoder().encodeToString(bytes))) {
            throw new IOException("invalid_image_base64");
        }
        if (!sha.equalsIgnoreCase(sha256(bytes))) throw new IOException("image_sha256_mismatch");
        boolean png = bytes.length >= 20 && bytes[0] == (byte) 0x89 && bytes[1] == 'P' &&
                bytes[2] == 'N' && bytes[3] == 'G' && bytes[4] == 13 && bytes[5] == 10 &&
                bytes[6] == 26 && bytes[7] == 10 &&
                bytes[bytes.length - 12] == 0 && bytes[bytes.length - 11] == 0 &&
                bytes[bytes.length - 10] == 0 && bytes[bytes.length - 9] == 0 &&
                bytes[bytes.length - 8] == 'I' && bytes[bytes.length - 7] == 'E' &&
                bytes[bytes.length - 6] == 'N' && bytes[bytes.length - 5] == 'D' &&
                bytes[bytes.length - 4] == (byte) 0xae && bytes[bytes.length - 3] == 0x42 &&
                bytes[bytes.length - 2] == 0x60 && bytes[bytes.length - 1] == (byte) 0x82;
        boolean jpeg = bytes.length >= 4 && bytes[0] == (byte) 0xff && bytes[1] == (byte) 0xd8 &&
                bytes[2] == (byte) 0xff && bytes[bytes.length - 2] == (byte) 0xff &&
                bytes[bytes.length - 1] == (byte) 0xd9;
        if (!("image/png".equals(mime) ? png : jpeg)) throw new IOException("image_mime_mismatch");
        return bytes;
    }

    static byte[] readBounded(InputStream input) throws IOException {
        if (input == null) throw new IOException("image_readback_unavailable");
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while (true) {
            if (Thread.currentThread().isInterrupted()) throw new IOException("image_import_interrupted");
            count = input.read(buffer, 0, Math.min(buffer.length, MAX_RAW_BYTES - output.size() + 1));
            if (count == -1) break;
            if (count == 0) throw new IOException("image_readback_unavailable");
            if (count > MAX_RAW_BYTES - output.size()) throw new IOException("image_size_out_of_bounds");
            output.write(buffer, 0, count);
        }
        if (output.size() == 0) throw new IOException("image_size_out_of_bounds");
        return output.toByteArray();
    }

    static String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder result = new StringBuilder(64);
            for (byte b : digest) result.append(String.format(Locale.ROOT, "%02x", b & 255));
            return result.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
