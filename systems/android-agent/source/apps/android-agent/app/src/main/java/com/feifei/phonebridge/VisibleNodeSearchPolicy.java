package com.feifei.phonebridge;

final class VisibleNodeSearchPolicy {
    private VisibleNodeSearchPolicy() {
    }

    static boolean shouldTraverse(boolean rootNode, boolean visibleToUser) {
        return rootNode || visibleToUser;
    }

    static boolean canMatch(boolean visibleToUser) {
        return visibleToUser;
    }

    static boolean canDispatch(boolean visibleToUser, boolean enabled) {
        return visibleToUser && enabled;
    }
}
