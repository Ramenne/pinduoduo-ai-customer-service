package com.feifei.phonebridge;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.concurrent.TimeUnit;
import okhttp3.Call;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

/** User-triggered HTTPS updates. Never reads or replaces device identity/state. */
final class AppUpdater {
    static final class Release {
        final int code;
        final long size;
        final String version, sha;
        Release(int code, long size, String version, String sha) {
            this.code=code; this.size=size; this.version=version; this.sha=sha;
        }
    }

    private final Context context;
    private final OkHttpClient client = new OkHttpClient.Builder()
            .followRedirects(false).followSslRedirects(false)
            .connectTimeout(15,TimeUnit.SECONDS).readTimeout(30,TimeUnit.SECONDS)
            .callTimeout(300,TimeUnit.SECONDS).build();
    private volatile Call active;
    private volatile boolean closed;

    AppUpdater(Context context) { this.context=context.getApplicationContext(); }

    Release check() throws Exception {
        Call call = client.newCall(new Request.Builder().url(CloudConfig.endpoint("api/v1/app-update")).header("Cache-Control","no-cache").build());
        call.timeout().timeout(20,TimeUnit.SECONDS);
        try(Response response=execute(call)) {
            if (!response.isSuccessful() || response.body()==null) throw new IOException("update_unavailable");
            ByteArrayOutputStream bytes=new ByteArrayOutputStream();
            try(InputStream in=response.body().byteStream()) {
                byte[] buffer=new byte[1024]; int count;
                while((count=in.read(buffer))!=-1) {
                    ensureOpen();
                    if(bytes.size()+count>8192) throw new IOException("update_metadata_too_large");
                    bytes.write(buffer,0,count);
                }
            }
            JSONObject value=new JSONObject(bytes.toString(StandardCharsets.UTF_8.name()));
            Object rawCode=value.get("version_code"), rawSize=value.get("size_bytes");
            if(!(rawCode instanceof Integer || rawCode instanceof Long) ||
                    !(rawSize instanceof Integer || rawSize instanceof Long) ||
                    !context.getPackageName().equals(value.getString("package_name"))) {
                throw new IOException("update_metadata_invalid");
            }
            long code=((Number)rawCode).longValue(), size=((Number)rawSize).longValue();
            String version=value.getString("version_name"), sha=value.getString("sha256");
            if(!AppUpdatePolicy.valid(code,size,version,sha)) throw new IOException("update_metadata_invalid");
            return new Release((int)code,size,version,sha);
        } finally { active=null; }
    }

    File download(Release release) throws Exception {return download(release,ignored->{});}
    File download(Release release,java.util.function.LongConsumer progress) throws Exception {
        if(!AppUpdatePolicy.newer(BuildConfig.VERSION_CODE,release.code)) throw new IOException("update_not_newer");
        File directory=new File(context.getCacheDir(),"app-updates");
        if(!directory.isDirectory() && !directory.mkdirs()) throw new IOException("update_cache_unavailable");
        File target=pendingFile(release),partial=new File(directory,"download-"+release.code+"-"+release.sha+".part");
        Call call=client.newCall(new Request.Builder().url(CloudConfig.endpoint("download/apk")+"?sha256="+release.sha).build());
        try(Response response=execute(call)) {
            if(!response.isSuccessful() || response.body()==null) throw new IOException("update_release_changed_or_unavailable");
            long declared=response.body().contentLength();
            if(declared!=-1 && declared!=release.size) throw new IOException("update_length_mismatch");
            try(InputStream in=response.body().byteStream(); FileOutputStream out=new FileOutputStream(partial)) {
                AppUpdatePayload.copy(in,out,release.size,release.sha,
                        () -> closed || Thread.currentThread().isInterrupted(),progress);
                out.getFD().sync();
            }
            ensureOpen(); verifyArchive(partial,release);
            Files.move(partial.toPath(),target.toPath(),StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
            JSONObject saved=new JSONObject().put("code",release.code).put("size",release.size)
                    .put("version",release.version).put("sha",release.sha);
            if(!context.getSharedPreferences("oin_update",Context.MODE_PRIVATE).edit()
                    .putString("ready",saved.toString()).commit()) throw new IOException("update_cache_unavailable");
            return target;
        } finally {
            active=null;
            // Only this updater's partial artifact, never user/device files.
            Files.deleteIfExists(partial.toPath());
        }
    }

    Release restore() throws Exception {
        String raw=context.getSharedPreferences("oin_update",Context.MODE_PRIVATE).getString("ready",null);
        if(raw==null)return null;
        try {
            JSONObject saved=new JSONObject(raw);
            Release release=new Release(saved.getInt("code"),saved.getLong("size"),saved.getString("version"),saved.getString("sha"));
            if(!AppUpdatePolicy.valid(release.code,release.size,release.version,release.sha) ||
                    !AppUpdatePolicy.newer(BuildConfig.VERSION_CODE,release.code)) {forget();return null;}
            File file=pendingFile(release);
            if(!file.isFile() || file.length()!=release.size){forget();return null;}
            try(InputStream in=new java.io.FileInputStream(file)) {
                AppUpdatePayload.copy(in,new java.io.OutputStream(){
                    @Override public void write(int value){}
                    @Override public void write(byte[] b,int off,int len){}
                },release.size,release.sha,()->closed||Thread.currentThread().isInterrupted());
            }
            verifyArchive(file,release);return release;
        } catch(Exception failure) {
            if(!closed)forget();
            throw failure;
        }
    }
    File pendingFile(Release release){return new File(context.getCacheDir(),"app-updates/oin-"+release.code+"-"+release.sha+".apk");}
    private void forget(){context.getSharedPreferences("oin_update",Context.MODE_PRIVATE).edit().remove("ready").apply();}

    private void verifyArchive(File file, Release release) throws Exception {
        PackageManager pm=context.getPackageManager();
        PackageInfo archive=pm.getPackageArchiveInfo(file.getAbsolutePath(),PackageManager.GET_SIGNING_CERTIFICATES);
        PackageInfo installed=pm.getPackageInfo(context.getPackageName(),PackageManager.GET_SIGNING_CERTIFICATES);
        if(archive==null || archive.applicationInfo==null || archive.applicationInfo.minSdkVersion>android.os.Build.VERSION.SDK_INT ||
                !AppUpdatePolicy.archiveMatches(archive.packageName,context.getPackageName(),BuildConfig.VERSION_CODE,
                        archive.getLongVersionCode(),release.code,archive.versionName,release.version,sameSigner(installed,archive))) {
            throw new IOException("update_package_or_signature_mismatch");
        }
    }

    private static boolean sameSigner(PackageInfo installed,PackageInfo archive) {
        if(installed.signingInfo==null || archive.signingInfo==null) return false;
        Signature[] a=installed.signingInfo.getApkContentsSigners(), b=archive.signingInfo.getApkContentsSigners();
        return a!=null && b!=null && a.length==1 && b.length==1 &&
                MessageDigest.isEqual(a[0].toByteArray(),b[0].toByteArray());
    }

    private Response execute(Call call) throws IOException {
        active=call; ensureOpen(); return call.execute();
    }
    private void ensureOpen() throws IOException {
        if(closed || Thread.currentThread().isInterrupted()) throw new IOException("update_cancelled");
    }
    void close() { closed=true; Call call=active; if(call!=null)call.cancel(); }
}
