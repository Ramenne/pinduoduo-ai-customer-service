package com.feifei.phonebridge;

final class AppUpdatePolicy {
    static final long MAX_BYTES = 32L * 1024 * 1024;
    private AppUpdatePolicy() {}

    static boolean valid(long code, long size, String version, String sha) {
        return code > 0 && code <= Integer.MAX_VALUE && size > 0 && size <= MAX_BYTES &&
                version != null && !version.isEmpty() && version.length() <= 100 &&
                sha != null && sha.matches("[0-9a-f]{64}");
    }

    static boolean newer(long installed, long candidate) {
        return candidate > installed && candidate <= Integer.MAX_VALUE;
    }

    static boolean archiveMatches(String packageName, String expectedPackage,
                                  long installed, long actualCode, long expectedCode,
                                  String actualVersion, String expectedVersion, boolean sameSigner) {
        return expectedPackage.equals(packageName) && actualCode == expectedCode &&
                newer(installed, actualCode) && expectedVersion.equals(actualVersion) && sameSigner;
    }
}
