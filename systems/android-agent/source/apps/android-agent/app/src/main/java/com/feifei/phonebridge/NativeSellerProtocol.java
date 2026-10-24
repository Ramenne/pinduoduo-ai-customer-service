package com.feifei.phonebridge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

final class NativeSellerProtocol {
    private NativeSellerProtocol() {}
    static String message(String device,String command,long stamp,String nonce,String payload) throws Exception {
        byte[] digest=MessageDigest.getInstance("SHA-256").digest(payload.getBytes(StandardCharsets.UTF_8));
        StringBuilder sha=new StringBuilder();
        for(byte b:digest)sha.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        return String.join("\n","oin-native-seller-v1",device,command,Long.toString(stamp),nonce,sha.toString());
    }
}
