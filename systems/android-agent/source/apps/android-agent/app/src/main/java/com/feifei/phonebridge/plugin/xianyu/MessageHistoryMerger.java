package com.feifei.phonebridge.plugin.xianyu;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Deterministic, bounded overlap merger for consecutive visible chat snapshots.
 *
 * <p>The cursor deliberately retains only candidate fingerprints and structural quality. It never
 * stores message text, media labels, member names, bounds, or platform-private identifiers. A
 * fingerprint is a de-duplication signal rather than a platform message id; ambiguous alignments
 * therefore preserve both sides and suppress new-message claims.</p>
 */
final class MessageHistoryMerger {
    static final int MAX_CURSOR_ENTRIES = 64;

    private MessageHistoryMerger() {
    }

    static MergeResult merge(
            String conversationKey,
            String identityQuality,
            boolean snapshotComplete,
            List<Entry> currentEntries,
            Cursor previous
    ) {
        String key = safe(conversationKey);
        String quality = safe(identityQuality);
        List<Entry> current = immutableEntries(currentEntries);
        List<Annotation> annotations = annotations(current.size(), "unclassified", false);

        if (key.isEmpty()) {
            return new MergeResult(
                    "identity_unavailable_no_merge",
                    annotations(current.size(), "identity_unavailable", true),
                    previous,
                    false,
                    0,
                    0,
                    current.size(),
                    0
            );
        }
        if (!snapshotComplete) {
            return new MergeResult(
                    "truncated_snapshot_not_merged",
                    annotations(current.size(), "truncated_snapshot", true),
                    previous,
                    false,
                    0,
                    0,
                    current.size(),
                    0
            );
        }
        if (current.isEmpty()) {
            return new MergeResult(
                    "empty_snapshot_not_advanced",
                    annotations,
                    previous,
                    false,
                    0,
                    0,
                    0,
                    0
            );
        }
        if (previous == null || previous.entries.isEmpty()) {
            Cursor baseline = cursor(key, quality, current, true);
            return new MergeResult(
                    "baseline_established",
                    annotations(current.size(), "baseline_existing", false),
                    baseline,
                    true,
                    0,
                    0,
                    0,
                    baseline.droppedEntryCount
            );
        }
        if (!key.equals(previous.conversationKey)) {
            Cursor baseline = cursor(key, quality, current, true);
            return new MergeResult(
                    "conversation_changed_baseline",
                    annotations(current.size(), "conversation_changed_baseline", false),
                    baseline,
                    true,
                    0,
                    0,
                    0,
                    baseline.droppedEntryCount
            );
        }

        OverlapSearch search = findBestOverlap(previous.entries, current);
        Set<Integer> fingerprintConflicts = fingerprintConflicts(previous.entries, current);
        if (search.length == 0) {
            boolean collision = !fingerprintConflicts.isEmpty();
            List<Entry> preserved = preserveBoth(previous.entries, current);
            Cursor next = cursor(key, quality, preserved, false);
            return new MergeResult(
                    collision ? "fingerprint_conflict_uncertain" : "no_overlap_uncertain",
                    annotations(current.size(),
                            collision ? "fingerprint_conflict" : "no_overlap", true),
                    next,
                    true,
                    0,
                    0,
                    current.size(),
                    next.droppedEntryCount
            );
        }
        if (search.ambiguous) {
            Cursor next = cursor(key, quality,
                    preserveBoth(previous.entries, current), false);
            return new MergeResult(
                    "ambiguous_overlap_preserved",
                    annotations(current.size(), "ambiguous_overlap", true),
                    next,
                    true,
                    search.length,
                    0,
                    current.size(),
                    next.droppedEntryCount
            );
        }

        Overlap overlap = search.best;
        boolean previousEndsAtOverlap = overlap.previousStart + overlap.length
                == previous.entries.size();
        boolean currentEndsAtOverlap = overlap.currentStart + overlap.length == current.size();
        boolean orderCompatible = (overlap.previousStart == 0 || overlap.currentStart == 0)
                && (previousEndsAtOverlap || currentEndsAtOverlap);
        int uncertainCount = 0;
        int newCandidateCount = 0;
        for (int index = 0; index < current.size(); index++) {
            boolean uncertain = fingerprintConflicts.contains(index);
            String state;
            boolean newCandidate = false;
            if (uncertain) {
                state = "fingerprint_conflict";
            } else if (index >= overlap.currentStart
                    && index < overlap.currentStart + overlap.length) {
                state = "overlap_confirmed";
            } else if (index < overlap.currentStart) {
                uncertain = overlap.previousStart != 0;
                state = uncertain ? "prefix_order_uncertain" : "historical_extension";
            } else if (previousEndsAtOverlap && previous.newestBoundaryConfirmed
                    && orderCompatible) {
                Entry entry = current.get(index);
                newCandidate = "incoming".equals(entry.direction);
                state = newCandidate ? "new_incoming_candidate" : "new_non_incoming_observation";
            } else {
                uncertain = true;
                state = "suffix_conflicts_with_known_boundary";
            }
            if (uncertain) {
                uncertainCount++;
                newCandidate = false;
            }
            if (newCandidate) {
                newCandidateCount++;
            }
            annotations.set(index, new Annotation(state, newCandidate, uncertain));
        }

        boolean safelyMergeable = fingerprintConflicts.isEmpty() && orderCompatible;
        List<Entry> mergedEntries = safelyMergeable
                ? mergeAligned(previous.entries, current, overlap)
                : preserveBoth(previous.entries, current);
        boolean boundaryConfirmed = safelyMergeable && previous.newestBoundaryConfirmed;
        Cursor next = cursor(key, quality, mergedEntries, boundaryConfirmed);

        String status;
        if (!fingerprintConflicts.isEmpty()) {
            status = "merged_with_fingerprint_conflict";
        } else if (!safelyMergeable) {
            status = "overlap_order_uncertain";
        } else if (overlap.previousStart == 0 && overlap.currentStart == 0
                && previous.entries.size() == current.size()
                && overlap.length == current.size()) {
            status = "duplicate_snapshot";
        } else if (newCandidateCount > 0
                || (previousEndsAtOverlap && !currentEndsAtOverlap)) {
            status = "newer_extension_merged";
        } else if (overlap.currentStart > 0 && overlap.previousStart == 0) {
            status = "historical_extension_merged";
        } else {
            status = "overlap_merged";
        }
        return new MergeResult(
                status,
                annotations,
                next,
                true,
                overlap.length,
                newCandidateCount,
                uncertainCount,
                next.droppedEntryCount
        );
    }

    private static OverlapSearch findBestOverlap(List<Entry> previous, List<Entry> current) {
        int bestLength = 0;
        int bestQuality = -1;
        List<Overlap> best = new ArrayList<>();
        for (int previousStart = 0; previousStart < previous.size(); previousStart++) {
            for (int currentStart = 0; currentStart < current.size(); currentStart++) {
                if (!previous.get(previousStart).equivalentTo(current.get(currentStart))) {
                    continue;
                }
                if (previousStart > 0 && currentStart > 0
                        && previous.get(previousStart - 1)
                        .equivalentTo(current.get(currentStart - 1))) {
                    continue;
                }
                int length = 0;
                int quality = 0;
                while (previousStart + length < previous.size()
                        && currentStart + length < current.size()
                        && previous.get(previousStart + length)
                        .equivalentTo(current.get(currentStart + length))) {
                    quality += qualityRank(previous.get(previousStart + length).fingerprintQuality);
                    quality += qualityRank(current.get(currentStart + length).fingerprintQuality);
                    length++;
                }
                boolean touchesWindowEdge = previousStart == 0 || currentStart == 0
                        || previousStart + length == previous.size()
                        || currentStart + length == current.size();
                if (!touchesWindowEdge) {
                    continue;
                }
                if (length > bestLength || (length == bestLength && quality > bestQuality)) {
                    bestLength = length;
                    bestQuality = quality;
                    best.clear();
                    best.add(new Overlap(previousStart, currentStart, length));
                } else if (length == bestLength && quality == bestQuality) {
                    best.add(new Overlap(previousStart, currentStart, length));
                }
            }
        }
        return new OverlapSearch(
                best.isEmpty() ? null : best.get(0),
                bestLength,
                best.size() > 1
        );
    }

    private static Set<Integer> fingerprintConflicts(
            List<Entry> previous,
            List<Entry> current
    ) {
        Set<Integer> conflicts = new HashSet<>();
        for (int currentIndex = 0; currentIndex < current.size(); currentIndex++) {
            Entry candidate = current.get(currentIndex);
            if (candidate.fingerprint.isEmpty()) {
                conflicts.add(currentIndex);
                continue;
            }
            for (Entry known : previous) {
                if (candidate.fingerprint.equals(known.fingerprint)
                        && !candidate.equivalentTo(known)) {
                    conflicts.add(currentIndex);
                    break;
                }
            }
        }
        return conflicts;
    }

    private static List<Entry> mergeAligned(
            List<Entry> previous,
            List<Entry> current,
            Overlap overlap
    ) {
        int previousEnd = overlap.previousStart + overlap.length;
        int currentEnd = overlap.currentStart + overlap.length;

        if (overlap.previousStart == 0 && overlap.currentStart == 0) {
            if (previousEnd == previous.size()) {
                return immutableEntries(current);
            }
            return immutableEntries(previous);
        }
        if (overlap.previousStart == 0) {
            List<Entry> merged = new ArrayList<>();
            merged.addAll(current.subList(0, overlap.currentStart));
            merged.addAll(previous);
            if (previousEnd == previous.size() && currentEnd < current.size()) {
                merged.addAll(current.subList(currentEnd, current.size()));
            }
            return immutableEntries(merged);
        }
        if (overlap.currentStart == 0) {
            if (previousEnd < previous.size() && currentEnd == current.size()) {
                return immutableEntries(previous);
            }
            List<Entry> merged = new ArrayList<>();
            merged.addAll(previous.subList(0, overlap.previousStart));
            merged.addAll(current);
            return immutableEntries(merged);
        }
        return preserveBoth(previous, current);
    }

    private static List<Entry> preserveBoth(List<Entry> previous, List<Entry> current) {
        List<Entry> preserved = new ArrayList<>();
        for (Entry entry : previous) {
            preserved.add(entry.asUncertain());
        }
        for (Entry entry : current) {
            preserved.add(entry.asUncertain());
        }
        return preserved;
    }

    private static Cursor cursor(
            String key,
            String quality,
            List<Entry> entries,
            boolean boundaryConfirmed
    ) {
        List<Entry> bounded = immutableEntries(entries);
        int dropped = Math.max(0, bounded.size() - MAX_CURSOR_ENTRIES);
        if (dropped > 0) {
            bounded = immutableEntries(bounded.subList(dropped, bounded.size()));
        }
        return new Cursor(key, quality, bounded, boundaryConfirmed, dropped);
    }

    private static List<Entry> immutableEntries(List<Entry> entries) {
        List<Entry> safe = new ArrayList<>();
        if (entries != null) {
            for (Entry entry : entries) {
                if (entry != null) {
                    safe.add(entry);
                }
            }
        }
        return Collections.unmodifiableList(safe);
    }

    private static List<Annotation> annotations(int size, String state, boolean uncertain) {
        List<Annotation> out = new ArrayList<>();
        for (int index = 0; index < size; index++) {
            out.add(new Annotation(state, false, uncertain));
        }
        return out;
    }

    private static int qualityRank(String quality) {
        String normalized = safe(quality);
        if (normalized.contains("time") || normalized.startsWith("medium")) {
            return 2;
        }
        return normalized.isEmpty() ? 0 : 1;
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }

    static final class Entry {
        final String fingerprint;
        final String direction;
        final String type;
        final String fingerprintQuality;
        final boolean uncertain;

        Entry(String fingerprint, String direction, String type, String fingerprintQuality) {
            this(fingerprint, direction, type, fingerprintQuality, false);
        }

        Entry(String fingerprint, String direction, String type, String fingerprintQuality,
              boolean uncertain) {
            this.fingerprint = safe(fingerprint);
            this.direction = safe(direction);
            this.type = safe(type);
            this.fingerprintQuality = safe(fingerprintQuality);
            this.uncertain = uncertain;
        }

        boolean equivalentTo(Entry other) {
            return other != null
                    && !fingerprint.isEmpty()
                    && fingerprint.equals(other.fingerprint)
                    && direction.equals(other.direction)
                    && type.equals(other.type);
        }

        Entry asUncertain() {
            return uncertain ? this : new Entry(
                    fingerprint, direction, type, fingerprintQuality, true);
        }
    }

    static final class Cursor {
        final String conversationKey;
        final String identityQuality;
        final List<Entry> entries;
        final boolean newestBoundaryConfirmed;
        final int droppedEntryCount;

        Cursor(String conversationKey, String identityQuality, List<Entry> entries,
               boolean newestBoundaryConfirmed) {
            this(conversationKey, identityQuality, entries, newestBoundaryConfirmed, 0);
        }

        private Cursor(String conversationKey, String identityQuality, List<Entry> entries,
                       boolean newestBoundaryConfirmed, int droppedEntryCount) {
            this.conversationKey = safe(conversationKey);
            this.identityQuality = safe(identityQuality);
            this.entries = immutableEntries(entries);
            this.newestBoundaryConfirmed = newestBoundaryConfirmed;
            this.droppedEntryCount = Math.max(0, droppedEntryCount);
        }
    }

    static final class Annotation {
        final String state;
        final boolean newCandidate;
        final boolean uncertain;

        Annotation(String state, boolean newCandidate, boolean uncertain) {
            this.state = state;
            this.newCandidate = newCandidate;
            this.uncertain = uncertain;
        }
    }

    static final class MergeResult {
        final String status;
        final List<Annotation> annotations;
        final Cursor nextCursor;
        final boolean cursorUpdated;
        final int overlapCount;
        final int newCandidateCount;
        final int uncertainCount;
        final int droppedCursorEntries;

        MergeResult(String status, List<Annotation> annotations, Cursor nextCursor,
                    boolean cursorUpdated, int overlapCount, int newCandidateCount,
                    int uncertainCount, int droppedCursorEntries) {
            this.status = status;
            this.annotations = Collections.unmodifiableList(new ArrayList<>(annotations));
            this.nextCursor = nextCursor;
            this.cursorUpdated = cursorUpdated;
            this.overlapCount = Math.max(0, overlapCount);
            this.newCandidateCount = Math.max(0, newCandidateCount);
            this.uncertainCount = Math.max(0, uncertainCount);
            this.droppedCursorEntries = Math.max(0, droppedCursorEntries);
        }
    }

    private static final class Overlap {
        final int previousStart;
        final int currentStart;
        final int length;

        Overlap(int previousStart, int currentStart, int length) {
            this.previousStart = previousStart;
            this.currentStart = currentStart;
            this.length = length;
        }
    }

    private static final class OverlapSearch {
        final Overlap best;
        final int length;
        final boolean ambiguous;

        OverlapSearch(Overlap best, int length, boolean ambiguous) {
            this.best = best;
            this.length = length;
            this.ambiguous = ambiguous;
        }
    }
}
