package com.feifei.phonebridge;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.concurrent.locks.ReentrantLock;
import java.util.concurrent.ConcurrentHashMap;

/** File-lock protected five-field lease shared by the main and guardian processes. */
final class RecoveryLeaseStore {
    interface Transaction<T> {
        T apply(RecoveryLease current) throws IOException;
    }

    private final File directory;
    private final File lockFile;
    private final File stateFile;
    private static final ConcurrentHashMap<String, ReentrantLock> PROCESS_LOCKS =
            new ConcurrentHashMap<>();
    private final ReentrantLock processLock;

    RecoveryLeaseStore(File directory) {
        this.directory = directory;
        this.lockFile = new File(directory, "lease.lock");
        this.stateFile = new File(directory, "lease.bin");
        String lockKey;
        try {
            lockKey = lockFile.getCanonicalPath();
        } catch (IOException unavailable) {
            lockKey = lockFile.getAbsolutePath();
        }
        this.processLock = PROCESS_LOCKS.computeIfAbsent(lockKey, ignored -> new ReentrantLock());
    }

    RecoveryLease beginProcess(String processInstanceId, long nowElapsed, long deadlineElapsed)
            throws IOException {
        return transact(current -> new RecoveryLease(
                current == null ? 1L : Math.max(1L, current.generation + 1L),
                processInstanceId,
                nowElapsed,
                deadlineElapsed,
                RecoveryLease.OWNER_MAIN));
    }

    RecoveryLease recordProgress(
            long generation,
            String processInstanceId,
            long nowElapsed,
            long deadlineElapsed
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(generation, processInstanceId) ||
                    !current.isOwnedByMain()) {
                return null;
            }
            return new RecoveryLease(generation, processInstanceId, nowElapsed, deadlineElapsed,
                    RecoveryLease.OWNER_MAIN);
        });
    }

    RecoveryLease recordAuthenticatedProgress(
            long generation,
            String processInstanceId,
            long nowElapsed,
            long deadlineElapsed
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(generation, processInstanceId) ||
                    (!RecoveryLease.OWNER_MAIN.equals(current.recoveryOwner) &&
                            !current.isGuardianFailureBlocked())) {
                return null;
            }
            return new RecoveryLease(generation, processInstanceId, nowElapsed, deadlineElapsed,
                    RecoveryLease.OWNER_MAIN);
        });
    }

    RecoveryLease claimExpired(
            long expectedGeneration,
            String expectedProcessInstanceId,
            long nowElapsed,
            long handoffDeadlineElapsed,
            String guardianOwner
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    !current.isGuardianClaimable() || nowElapsed < current.deadlineElapsed ||
                    guardianOwner == null ||
                    !guardianOwner.startsWith(RecoveryLease.OWNER_GUARDIAN_PENDING_PREFIX) ||
                    handoffDeadlineElapsed <= nowElapsed) {
                return null;
            }
            return new RecoveryLease(
                    current.generation,
                    current.processInstanceId,
                    nowElapsed,
                    handoffDeadlineElapsed,
                    guardianOwner);
        });
    }

    RecoveryLease resumeExactClaim(
            long expectedGeneration,
            String expectedProcessInstanceId,
            String guardianOwner,
            long nowElapsed,
            long deadlineElapsed
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    guardianOwner == null || !guardianOwner.equals(current.recoveryOwner) ||
                    !current.isGuardianPending()) {
                return null;
            }
            if (nowElapsed > current.deadlineElapsed) {
                return new RecoveryLease(
                        current.generation,
                        current.processInstanceId,
                        current.lastProgressElapsed,
                        current.deadlineElapsed,
                        RecoveryLease.OWNER_GUARDIAN_HANDOFF_TIMEOUT);
            }
            return new RecoveryLease(
                    current.generation,
                    current.processInstanceId,
                    nowElapsed,
                    deadlineElapsed,
                    RecoveryLease.OWNER_MAIN);
        });
    }

    RecoveryLease failExactGuardianHandoff(
            long expectedGeneration,
            String expectedProcessInstanceId,
            String expectedOwner,
            String blockedOwner
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    expectedOwner == null || !expectedOwner.equals(current.recoveryOwner) ||
                    !current.isGuardianPending() ||
                    (!RecoveryLease.OWNER_GUARDIAN_START_REJECTED.equals(blockedOwner) &&
                            !RecoveryLease.OWNER_GUARDIAN_HANDOFF_TIMEOUT.equals(blockedOwner) &&
                            !RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE.equals(blockedOwner))) {
                return null;
            }
            return new RecoveryLease(
                    current.generation,
                    current.processInstanceId,
                    current.lastProgressElapsed,
                    current.deadlineElapsed,
                    blockedOwner);
        });
    }

    RecoveryLease expireExactGuardianHandoff(
            long expectedGeneration,
            String expectedProcessInstanceId,
            String expectedOwner,
            long nowElapsed
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    expectedOwner == null || !expectedOwner.equals(current.recoveryOwner) ||
                    !current.isGuardianPending() || nowElapsed < current.deadlineElapsed) {
                return null;
            }
            return new RecoveryLease(
                    current.generation,
                    current.processInstanceId,
                    current.lastProgressElapsed,
                    current.deadlineElapsed,
                    RecoveryLease.OWNER_GUARDIAN_HANDOFF_TIMEOUT);
        });
    }

    RecoveryLease requestImmediateRecovery(
            long expectedGeneration,
            String expectedProcessInstanceId,
            long nowElapsed,
            String requestOwner
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    !current.isOwnedByMain() || requestOwner == null ||
                    !requestOwner.startsWith(RecoveryLease.OWNER_MAIN_RECOVERY_PREFIX)) {
                return null;
            }
            return new RecoveryLease(
                    current.generation,
                    current.processInstanceId,
                    current.lastProgressElapsed,
                    nowElapsed,
                    requestOwner);
        });
    }

    RecoveryLease failImmediateRecovery(
            long expectedGeneration,
            String expectedProcessInstanceId,
            String expectedRequestOwner
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    expectedRequestOwner == null ||
                    !expectedRequestOwner.equals(current.recoveryOwner) ||
                    !current.recoveryOwner.startsWith(RecoveryLease.OWNER_MAIN_RECOVERY_PREFIX)) {
                return null;
            }
            return new RecoveryLease(
                    current.generation,
                    current.processInstanceId,
                    current.lastProgressElapsed,
                    current.deadlineElapsed,
                    RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE);
        });
    }

    RecoveryLease blockExpiredMainAfterGuardianFailure(
            long expectedGeneration,
            String expectedProcessInstanceId,
            long nowElapsed,
            String blockedOwner
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    !current.isGuardianClaimable() || nowElapsed < current.deadlineElapsed ||
                    !RecoveryLease.OWNER_GUARDIAN_INTERNAL_FAILURE.equals(blockedOwner)) {
                return null;
            }
            return new RecoveryLease(
                    current.generation,
                    current.processInstanceId,
                    current.lastProgressElapsed,
                    current.deadlineElapsed,
                    blockedOwner);
        });
    }

    RecoveryLease blockGeneration(
            long expectedGeneration,
            String expectedProcessInstanceId,
            String blockedOwner
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    RecoveryLease.OWNER_STOPPED.equals(current.recoveryOwner)) {
                return null;
            }
            boolean terminalRequested = RecoveryLease.OWNER_TERMINAL_BLOCKED.equals(blockedOwner);
            boolean terminalCurrent = RecoveryLease.OWNER_TERMINAL_BLOCKED.equals(
                    current.recoveryOwner);
            boolean networkRequested = RecoveryLease.OWNER_NETWORK_BLOCKED.equals(blockedOwner);
            if ((!terminalRequested && !networkRequested) ||
                    (terminalCurrent && !terminalRequested)) {
                return null;
            }
            if (blockedOwner.equals(current.recoveryOwner)) {
                return current;
            }
            // A terminal block has highest priority. A network block may replace MAIN or an
            // in-flight guardian claim, but can never revive or downgrade terminal/stopped.
            return new RecoveryLease(current.generation, current.processInstanceId,
                    current.lastProgressElapsed, current.deadlineElapsed, blockedOwner);
        });
    }

    RecoveryLease resumeBlocked(
            long expectedGeneration,
            String expectedProcessInstanceId,
            String blockedOwner,
            long nowElapsed,
            long deadlineElapsed
    ) throws IOException {
        return transact(current -> {
            if (current == null || !current.belongsTo(
                    expectedGeneration, expectedProcessInstanceId) ||
                    !blockedOwner.equals(current.recoveryOwner)) {
                return null;
            }
            return new RecoveryLease(current.generation, current.processInstanceId,
                    nowElapsed, deadlineElapsed, RecoveryLease.OWNER_MAIN);
        });
    }

    boolean stop(long generation, String processInstanceId) throws IOException {
        RecoveryLease stopped = transact(current -> {
            if (current == null || !current.belongsTo(generation, processInstanceId)) {
                return null;
            }
            return new RecoveryLease(
                    current.generation,
                    current.processInstanceId,
                    current.lastProgressElapsed,
                    current.deadlineElapsed,
                    RecoveryLease.OWNER_STOPPED);
        });
        return stopped != null;
    }

    RecoveryLease read() throws IOException {
        return transact(current -> current);
    }

    private RecoveryLease transact(Transaction<RecoveryLease> operation) throws IOException {
        ensureDirectory();
        processLock.lock();
        try {
            try (RandomAccessFile lockAccess = new RandomAccessFile(lockFile, "rw");
                 FileChannel channel = lockAccess.getChannel();
                 FileLock ignored = channel.lock()) {
                RecoveryLease current = readUnlocked();
                RecoveryLease result = operation.apply(current);
                if (result != null && result != current) {
                    writeUnlocked(result);
                }
                return result;
            }
        } finally {
            processLock.unlock();
        }
    }

    private void ensureDirectory() throws IOException {
        if (!directory.exists() && !directory.mkdirs() && !directory.isDirectory()) {
            throw new IOException("recovery_lease_directory_unavailable");
        }
    }

    private RecoveryLease readUnlocked() throws IOException {
        if (!stateFile.isFile()) {
            return null;
        }
        try (DataInputStream input = new DataInputStream(
                new BufferedInputStream(new FileInputStream(stateFile)))) {
            int version = input.readInt();
            if (version != 1) {
                throw new IOException("unsupported_recovery_lease_version");
            }
            RecoveryLease lease = new RecoveryLease(
                    input.readLong(),
                    input.readUTF(),
                    input.readLong(),
                    input.readLong(),
                    input.readUTF());
            if (input.read() != -1) {
                throw new IOException("recovery_lease_trailing_data");
            }
            return lease;
        } catch (EOFException truncated) {
            throw new IOException("truncated_recovery_lease", truncated);
        }
    }

    private void writeUnlocked(RecoveryLease lease) throws IOException {
        File temporary = new File(directory, "lease.bin.tmp");
        try (FileOutputStream file = new FileOutputStream(temporary);
             DataOutputStream output = new DataOutputStream(new BufferedOutputStream(file))) {
            output.writeInt(1);
            output.writeLong(lease.generation);
            output.writeUTF(lease.processInstanceId);
            output.writeLong(lease.lastProgressElapsed);
            output.writeLong(lease.deadlineElapsed);
            output.writeUTF(lease.recoveryOwner);
            output.flush();
            file.getFD().sync();
        }
        Files.move(temporary.toPath(), stateFile.toPath(),
                StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
}
