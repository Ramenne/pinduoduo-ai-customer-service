package com.feifei.phonebridge;

/** Geometry is derived from a freshly unique matched node, never cloud coordinates. */
final class NodeTapPolicy {
    private NodeTapPolicy() {}

    static boolean safe(boolean visible, boolean enabled, boolean password,
                        int left, int top, int right, int bottom,
                        int surfaceLeft, int surfaceTop, int surfaceRight, int surfaceBottom) {
        long width = (long) right - left, height = (long) bottom - top;
        long surfaceWidth = (long) surfaceRight - surfaceLeft;
        long surfaceHeight = (long) surfaceBottom - surfaceTop;
        return visible && enabled && !password && width >= 2 && height >= 2 &&
                surfaceWidth > 0 && surfaceHeight > 0 && surfaceWidth <= 32768 &&
                surfaceHeight <= 32768 && left >= surfaceLeft &&
                top >= surfaceTop && right <= surfaceRight && bottom <= surfaceBottom &&
                width * height <= surfaceWidth * surfaceHeight / 2;
    }
}
