package com.feifei.phonebridge;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.function.BooleanSupplier;

final class AppUpdatePayload {
    static void copy(InputStream in, OutputStream out, long size, String sha, BooleanSupplier cancelled)
            throws IOException {
        copy(in,out,size,sha,cancelled,ignored->{});
    }
    static void copy(InputStream in, OutputStream out, long size, String sha, BooleanSupplier cancelled,
                     java.util.function.LongConsumer progress) throws IOException {
        if(size<1 || size>AppUpdatePolicy.MAX_BYTES || sha==null || !sha.matches("[0-9a-f]{64}"))
            throw new IOException("update_metadata_invalid");
        MessageDigest digest;
        try { digest=MessageDigest.getInstance("SHA-256"); }
        catch(NoSuchAlgorithmException unavailable) { throw new IOException("update_digest_unavailable",unavailable); }
        long total=0; byte[] buffer=new byte[16384];
        while(true) {
            if(cancelled.getAsBoolean())throw new IOException("update_cancelled");
            int count=in.read(buffer);
            if(count==-1)break;
            if(cancelled.getAsBoolean())throw new IOException("update_cancelled");
            total+=count;
            if(total>size || total>AppUpdatePolicy.MAX_BYTES)throw new IOException("update_size_exceeded");
            digest.update(buffer,0,count);out.write(buffer,0,count);progress.accept(total);
        }
        StringBuilder actual=new StringBuilder();
        for(byte b:digest.digest())actual.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        if(total!=size || !sha.contentEquals(actual))throw new IOException("update_digest_mismatch");
    }
}
