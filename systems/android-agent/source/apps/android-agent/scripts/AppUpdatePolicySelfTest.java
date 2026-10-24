package com.feifei.phonebridge;

public final class AppUpdatePolicySelfTest {
    public static void main(String[] args) throws Exception {
        String digest="a".repeat(64);
        check(AppUpdatePolicy.valid(37,1,"0.11.2-v01",digest));
        check(!AppUpdatePolicy.valid(0,1,"v",digest));
        check(!AppUpdatePolicy.valid(37,0,"v",digest));
        check(!AppUpdatePolicy.valid(37,AppUpdatePolicy.MAX_BYTES+1,"v",digest));
        check(!AppUpdatePolicy.valid(37,1,"v","bad"));
        check(AppUpdatePolicy.newer(36,37));
        check(!AppUpdatePolicy.newer(37,37));
        check(!AppUpdatePolicy.newer(37,36));
        check(AppUpdatePolicy.archiveMatches("oin","oin",36,37,37,"v","v",true));
        check(!AppUpdatePolicy.archiveMatches("other","oin",36,37,37,"v","v",true));
        check(!AppUpdatePolicy.archiveMatches("oin","oin",36,37,38,"v","v",true));
        check(!AppUpdatePolicy.archiveMatches("oin","oin",36,37,37,"v","wrong",true));
        check(!AppUpdatePolicy.archiveMatches("oin","oin",36,37,37,"v","v",false));
        byte[] body="abc".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        String sha="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
        java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream();
        AppUpdatePayload.copy(new java.io.ByteArrayInputStream(body),out,3,sha,()->false);
        check(java.util.Arrays.equals(out.toByteArray(),body));
        final long[] downloaded={0};
        AppUpdatePayload.copy(new java.io.ByteArrayInputStream(body),new java.io.ByteArrayOutputStream(),3,sha,()->false,n->{check(n>=downloaded[0]&&n<=3);downloaded[0]=n;});
        check(downloaded[0]==3);
        fails(body,2,sha,false,"update_size_exceeded");
        fails(body,4,sha,false,"update_digest_mismatch");
        fails(body,3,"0".repeat(64),false,"update_digest_mismatch");
        fails(body,3,sha,true,"update_cancelled");
        System.out.println("APP_UPDATE_POLICY_SELF_TEST_PASS");
    }
    private static void fails(byte[] body,long size,String sha,boolean cancel,String expected) throws Exception {
        try { AppUpdatePayload.copy(new java.io.ByteArrayInputStream(body),new java.io.ByteArrayOutputStream(),size,sha,()->cancel);
            throw new AssertionError("failure expected");
        } catch(java.io.IOException failure) { check(expected.equals(failure.getMessage())); }
    }
    private static void check(boolean condition) { if(!condition)throw new AssertionError(); }
}
