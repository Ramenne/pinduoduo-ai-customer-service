package com.feifei.phonebridge;

/** Pure state fence for transport, task-channel and device-state lifecycles. */
final class ChannelLaneState {
    static final String DEVICE_FRESH = "fresh";
    static final String DEVICE_STALE = "stale";

    static final class Snapshot {
        final long epoch;
        final boolean transportAuthenticated;
        final boolean taskChannelReady;
        final String taskChannelState;
        final String deviceState;

        Snapshot(
                long epoch,
                boolean transportAuthenticated,
                boolean taskChannelReady,
                String taskChannelState,
                String deviceState
        ) {
            this.epoch = epoch;
            this.transportAuthenticated = transportAuthenticated;
            this.taskChannelReady = taskChannelReady;
            this.taskChannelState = taskChannelState;
            this.deviceState = deviceState;
        }
    }

    private long epoch = -1L;
    private boolean transportAuthenticated;
    private boolean taskChannelReady;
    private String taskChannelState = "unavailable";
    private String deviceState = DEVICE_STALE;

    synchronized void authenticated(
            long authenticatedEpoch,
            boolean ready,
            String state
    ) {
        epoch = authenticatedEpoch;
        transportAuthenticated = true;
        taskChannelReady = ready;
        taskChannelState = state;
        deviceState = DEVICE_STALE;
    }

    synchronized boolean updateTaskChannel(long updateEpoch, boolean ready, String state) {
        if (!transportAuthenticated || epoch != updateEpoch) {
            return false;
        }
        taskChannelReady = ready;
        taskChannelState = state;
        return true;
    }

    synchronized boolean updateDeviceState(long updateEpoch, String state) {
        if (!transportAuthenticated || epoch != updateEpoch) {
            return false;
        }
        deviceState = state;
        return true;
    }

    synchronized void transportClosed(long closedEpoch) {
        if (epoch == closedEpoch) {
            transportAuthenticated = false;
            taskChannelReady = false;
        }
    }

    synchronized boolean isTaskChannelReady() {
        return transportAuthenticated && taskChannelReady;
    }

    synchronized Snapshot snapshot() {
        return new Snapshot(
                epoch,
                transportAuthenticated,
                taskChannelReady,
                taskChannelState,
                deviceState
        );
    }
}
