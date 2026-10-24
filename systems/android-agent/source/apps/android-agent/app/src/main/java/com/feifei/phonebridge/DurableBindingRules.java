package com.feifei.phonebridge;

/** Android-free verification rule for the synchronous binding commit. */
final class DurableBindingRules {
    private DurableBindingRules() {
    }

    static boolean isDurablyPersisted(
            boolean commitReturned,
            String expectedAccount,
            String expectedDevice,
            String storedAccount,
            String storedDevice,
            String storedState
    ) {
        return commitReturned &&
                expectedAccount != null && !expectedAccount.isEmpty() &&
                expectedDevice != null && !expectedDevice.isEmpty() &&
                expectedAccount.equals(storedAccount) &&
                expectedDevice.equals(storedDevice) &&
                CloudConnectionLifecycle.BOUND_OFFLINE.equals(storedState);
    }

    static boolean sameOwner(String storedOwner, String authenticatedOwner) {
        return storedOwner != null && !storedOwner.isEmpty() &&
                storedOwner.equals(authenticatedOwner);
    }
}
