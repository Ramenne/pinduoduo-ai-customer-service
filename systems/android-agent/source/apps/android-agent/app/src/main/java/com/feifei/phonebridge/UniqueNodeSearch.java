package com.feifei.phonebridge;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.function.LongSupplier;

/** Owns every acquired node; caller owns only Result.node until release. */
final class UniqueNodeSearch {
    interface Access<N> {
        boolean matches(N node);
        int childCount(N node);
        N child(N node, int index);
        void release(N node);
    }

    static final class Result<N> {
        N node;
        int matches;
        int visited;
        boolean truncated;
    }

    private UniqueNodeSearch() {}

    static <N> Result<N> find(N root, Access<N> access, int limit, long deadline, LongSupplier clock) {
        Result<N> result = new Result<>();
        Deque<N> queue = new ArrayDeque<>();
        queue.add(root);
        int acquired = 1;
        boolean completed = false;
        try {
            while (!queue.isEmpty()) {
                if (result.visited >= limit || clock.getAsLong() >= deadline ||
                        Thread.currentThread().isInterrupted()) {
                    result.truncated = true;
                    break;
                }
                N node = queue.removeFirst();
                boolean retained = false;
                try {
                    result.visited++;
                    if (access.matches(node)) {
                        result.matches++;
                        if (result.node == null) {
                            result.node = node;
                            retained = true;
                        }
                    }
                    int children = access.childCount(node);
                    // Traverse hidden parents too: hidden parent does not prove hidden descendants.
                    for (int i = 0; i < children; i++) {
                        if (acquired >= limit || clock.getAsLong() >= deadline ||
                                Thread.currentThread().isInterrupted()) {
                            result.truncated = true;
                            break;
                        }
                        N child = access.child(node, i);
                        acquired++; // Bound child lookups as well as queue memory, even null children.
                        if (child != null) queue.addLast(child);
                    }
                } finally {
                    if (!retained) access.release(node);
                }
                if (result.truncated) break;
            }
            completed = true;
            return result;
        } finally {
            while (!queue.isEmpty()) access.release(queue.removeFirst());
            if (!completed || result.truncated || result.matches != 1) {
                if (result.node != null) access.release(result.node);
                result.node = null;
            }
        }
    }
}
