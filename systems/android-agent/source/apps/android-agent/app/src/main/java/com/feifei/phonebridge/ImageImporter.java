package com.feifei.phonebridge;

import android.Manifest;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.SystemClock;
import android.provider.MediaStore;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Locale;

/** Serialized, content-addressed image import. Does not select/upload/publish a product. */
final class ImageImporter {
    private static final String DIRECTORY = "Pictures/OIN/";
    // Bound decompression independently from the compressed byte cap (16 MiB ARGB maximum).
    private static final long MAX_PIXELS = 4L * 1024 * 1024;
    private ImageImporter() {}

    static synchronized JSONObject importImage(Context context, JSONObject request, long remainingBudgetMillis) throws JSONException {
        Uri inserted = null;
        ContentResolver resolver = context.getContentResolver();
        long deadline = SystemClock.elapsedRealtime() + Math.min(10000L, Math.max(0L, remainingBudgetMillis));
        try {
            ensureActive(deadline);
            String mime = request.getString("mime_type");
            String filename = request.getString("filename");
            String sha = request.getString("sha256").toLowerCase(Locale.ROOT);
            byte[] bytes = ImageImportContract.decode(mime, filename, sha, request.getString("data_base64"));
            validateBitmap(bytes, mime);
            ensureActive(deadline);
            if (Build.VERSION.SDK_INT <= 28 && (
                    context.checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED ||
                    context.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED)) {
                return ActionDispatcher.outcome(false, "image_storage_permission_required");
            }
            if (!Environment.MEDIA_MOUNTED.equals(Environment.getExternalStorageState())) {
                return ActionDispatcher.outcome(false, "image_storage_unavailable");
            }
            Uri collection = Build.VERSION.SDK_INT >= 29
                    ? MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
                    : MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
            String pathColumn = Build.VERSION.SDK_INT >= 29
                    ? MediaStore.Images.Media.RELATIVE_PATH : MediaStore.Images.Media.DATA;
            File legacyDirectory = Build.VERSION.SDK_INT <= 28 ? new File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES), "OIN") : null;
            String location = legacyDirectory == null ? DIRECTORY : new File(legacyDirectory, filename).getAbsolutePath();
            String selection = MediaStore.Images.Media.DISPLAY_NAME + "=? AND " + pathColumn + "=?";
            Uri existing = null;
            try (Cursor cursor = query(resolver, collection, new String[]{MediaStore.Images.Media._ID},
                    selection, new String[]{filename, location})) {
                if (cursor == null) throw new IOException("image_store_query_failed");
                if (cursor.getCount() > 1) throw new IOException("image_existing_not_unique");
                if (cursor.moveToFirst()) existing = ContentUris.withAppendedId(collection, cursor.getLong(0));
            }
            if (existing != null) {
                // Includes pending rows after process death. Never insert another copy or overwrite.
                verifyStored(resolver, existing, filename, mime, sha, pathColumn, location);
                ensureActive(deadline);
                publish(resolver, existing);
                verifyStored(resolver, existing, filename, mime, sha, pathColumn, location);
                return verified(filename, sha, existing);
            }
            if (legacyDirectory != null) {
                if (!legacyDirectory.isDirectory() && !legacyDirectory.mkdirs()) {
                    throw new IOException("image_directory_unavailable");
                }
                if (new File(legacyDirectory, filename).exists()) {
                    throw new IOException("image_existing_file_not_indexed");
                }
            }
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.DISPLAY_NAME, filename);
            values.put(MediaStore.Images.Media.MIME_TYPE, mime);
            values.put(pathColumn, location);
            if (Build.VERSION.SDK_INT >= 29) values.put(MediaStore.Images.Media.IS_PENDING, 1);
            ensureActive(deadline);
            inserted = resolver.insert(collection, values);
            if (inserted == null) throw new IOException("image_store_insert_failed");
            try (OutputStream output = resolver.openOutputStream(inserted, "w")) {
                if (output == null) throw new IOException("image_store_write_failed");
                output.write(bytes);
                output.flush();
            }
            verifyStored(resolver, inserted, filename, mime, sha, pathColumn, location);
            ensureActive(deadline);
            publish(resolver, inserted);
            verifyStored(resolver, inserted, filename, mime, sha, pathColumn, location);
            JSONObject result = verified(filename, sha, inserted);
            inserted = null; // Ownership transferred to a verified public media row.
            return result;
        } catch (SecurityException denied) {
            return ActionDispatcher.outcome(false, "image_storage_permission_denied");
        } catch (IOException invalid) {
            String message = invalid.getMessage();
            return ActionDispatcher.outcome(false, message != null && message.matches("image_[a-z0-9_]{1,55}")
                    ? message : "image_import_failed");
        } catch (RuntimeException unavailable) {
            return ActionDispatcher.outcome(false, "image_import_unavailable");
        } finally {
            // Only remove the row created by this invocation. Existing media is never deleted.
            if (inserted != null) {
                try { resolver.delete(inserted, null, null); } catch (RuntimeException ignored) {
                    // A surviving row is found next time and fails closed unless its bytes verify.
                }
            }
        }
    }

    private static void ensureActive(long deadline) throws IOException {
        if (Thread.currentThread().isInterrupted()) throw new IOException("image_import_interrupted");
        if (SystemClock.elapsedRealtime() >= deadline) throw new IOException("image_import_budget_exhausted");
    }

    private static Cursor query(ContentResolver resolver, Uri uri, String[] projection,
                                String selection, String[] arguments) {
        if (Build.VERSION.SDK_INT >= 30) {
            Bundle options = new Bundle();
            options.putInt(MediaStore.QUERY_ARG_MATCH_PENDING, MediaStore.MATCH_INCLUDE);
            if (selection != null) {
                options.putString(ContentResolver.QUERY_ARG_SQL_SELECTION, selection);
                options.putStringArray(ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS, arguments);
            }
            return resolver.query(uri, projection, options, null);
        }
        return resolver.query(Build.VERSION.SDK_INT >= 29 ? MediaStore.setIncludePending(uri) : uri,
                projection, selection, arguments, null);
    }

    private static void verifyStored(ContentResolver resolver, Uri uri, String filename, String mime,
                                     String sha, String pathColumn, String location) throws IOException {
        try (Cursor cursor = query(resolver, uri, new String[]{MediaStore.Images.Media.DISPLAY_NAME,
                MediaStore.Images.Media.MIME_TYPE, pathColumn}, null, null)) {
            if (cursor == null || cursor.getCount() != 1 || !cursor.moveToFirst() ||
                    !filename.equals(cursor.getString(0)) || !mime.equals(cursor.getString(1)) ||
                    !location.equals(cursor.getString(2))) throw new IOException("image_metadata_not_verified");
        }
        try (InputStream input = resolver.openInputStream(uri)) {
            byte[] actual = ImageImportContract.readBounded(input);
            if (!sha.equals(ImageImportContract.sha256(actual))) throw new IOException("image_readback_sha256_mismatch");
        }
    }

    private static void publish(ContentResolver resolver, Uri uri) throws IOException {
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.IS_PENDING, 0);
            if (resolver.update(uri, values, null, null) != 1) throw new IOException("image_store_publish_failed");
            try (Cursor cursor = resolver.query(uri, new String[]{MediaStore.Images.Media.IS_PENDING}, null, null, null)) {
                if (cursor == null || !cursor.moveToFirst() || cursor.getInt(0) != 0) {
                    throw new IOException("image_store_publish_not_verified");
                }
            }
        }
    }

    private static void validateBitmap(byte[] bytes, String mime) throws IOException {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(bytes, 0, bytes.length, bounds);
        if (!mime.equals(bounds.outMimeType) || bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            throw new IOException("image_decode_failed");
        }
        if (bounds.outWidth > 8192 || bounds.outHeight > 8192 ||
                (long) bounds.outWidth * bounds.outHeight > MAX_PIXELS) throw new IOException("image_pixel_limit_exceeded");
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inPreferredConfig = Bitmap.Config.ARGB_8888;
        Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
        if (bitmap == null) throw new IOException("image_decode_failed");
        try {
            if (bitmap.getWidth() != bounds.outWidth || bitmap.getHeight() != bounds.outHeight ||
                    !mime.equals(options.outMimeType)) throw new IOException("image_decode_failed");
        } finally {
            bitmap.recycle();
        }
    }

    private static JSONObject verified(String filename, String sha, Uri uri) throws JSONException {
        return ActionDispatcher.outcome(true, "image_import_verified")
                .put("filename", filename).put("sha256", sha).put("media_uri", uri.toString()).put("verified", true);
    }
}
