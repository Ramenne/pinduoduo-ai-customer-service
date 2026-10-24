package com.feifei.phonebridge;

public final class DurableBindingRulesSelfTest {
    private DurableBindingRulesSelfTest() {
    }

    public static void main(String[] args) {
        assertTrue(DurableBindingRules.isDurablyPersisted(
                true, "owner", "device-1", "owner", "device-1",
                CloudConnectionLifecycle.BOUND_OFFLINE
        ), "successful commit with exact readback is durable");
        assertFalse(DurableBindingRules.isDurablyPersisted(
                false, "owner", "device-1", "owner", "device-1",
                CloudConnectionLifecycle.BOUND_OFFLINE
        ), "failed commit cannot be reported as a binding success");
        assertFalse(DurableBindingRules.isDurablyPersisted(
                true, "owner", "device-1", "owner", "device-old",
                CloudConnectionLifecycle.BOUND_OFFLINE
        ), "mismatched readback cannot be reported as a binding success");
        assertFalse(DurableBindingRules.isDurablyPersisted(
                true, "owner", "device-1", "owner", "device-1", "ONLINE_FRESH"
        ), "binding transaction must read back its initial offline state");
        assertTrue(DurableBindingRules.sameOwner("owner", "owner"),
                "same-owner rebind and credential repair remain allowed");
        assertFalse(DurableBindingRules.sameOwner("owner-a", "owner-b"),
                "cross-owner rebind must fail before creating a new scope");
        assertFalse(DurableBindingRules.sameOwner("", "owner"),
                "missing durable owner cannot authorize identity replacement");
        System.out.println("Durable binding rules self-test: PASS");
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }

    private static void assertFalse(boolean value, String message) {
        assertTrue(!value, message);
    }
}
