package com.feifei.phonebridge;

public final class NativeSellerProtocolSelfTest {
    public static void main(String[] args) throws Exception {
        String expected="oin-native-seller-v1\ndevice\ninventory\n123\nnonce\n44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a";
        if(!expected.equals(NativeSellerProtocol.message("device","inventory",123,"nonce","{}")))throw new AssertionError("byte contract mismatch");
        if(expected.equals(NativeSellerProtocol.message("device","start",123,"nonce","{}")))throw new AssertionError("command not bound");
        if(expected.equals(NativeSellerProtocol.message("other","inventory",123,"nonce","{}")))throw new AssertionError("device not bound");
        if(expected.equals(NativeSellerProtocol.message("device","inventory",123,"nonce","{ }")))throw new AssertionError("exact bytes not bound");
        System.out.println("NATIVE_SELLER_PROTOCOL_SELF_TEST_PASS checks=4");
    }
}
