package com.feifei.phonebridge;

import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.content.Context;

/** Serial system-I/O boundary for registering and unregistering the network callback. */
final class NetworkCallbackIoLane {
    interface SignalSink {
        void signal(NetworkCallbackIoLane source, boolean usable);
    }

    interface Completion {
        void complete(long generation, boolean success);
    }

    private final Context context;
    private ConnectivityManager manager;
    private final SignalSink signalSink;
    private final ConnectivityManager.NetworkCallback callback;
    private final BoundedIoLane lane = new BoundedIoLane("oin-network-callback-io", 4);
    private boolean actuallyRegistered;
    private volatile boolean abandoned;
    private volatile Boolean latestUsableNetwork;

    NetworkCallbackIoLane(
            Context context,
            SignalSink signalSink
    ) {
        this.context = context.getApplicationContext();
        this.signalSink = signalSink;
        this.callback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                publishSignal(true);
            }

            @Override
            public void onLost(Network network) {
                publishSignal(false);
            }

            @Override
            public void onCapabilitiesChanged(
                    Network network,
                    NetworkCapabilities capabilities
            ) {
                publishSignal(capabilities != null && capabilities.hasCapability(
                        NetworkCapabilities.NET_CAPABILITY_INTERNET));
            }
        };
    }

    private void publishSignal(boolean usable) {
        latestUsableNetwork = usable;
        signalSink.signal(this, usable);
    }

    Boolean latestUsableNetwork() {
        return latestUsableNetwork;
    }

    boolean register(long generation, Completion completion) {
        return lane.submit(() -> {
            boolean success = false;
            try {
                if (manager == null) {
                    manager = context.getSystemService(ConnectivityManager.class);
                }
                if (manager != null && !actuallyRegistered) {
                    manager.registerDefaultNetworkCallback(callback);
                    actuallyRegistered = true;
                }
                success = manager != null && actuallyRegistered;
            } catch (RuntimeException ignored) {
                actuallyRegistered = false;
            }
            if (success) {
                try {
                    Network active = manager.getActiveNetwork();
                    NetworkCapabilities capabilities = active == null
                            ? null : manager.getNetworkCapabilities(active);
                    latestUsableNetwork = capabilities != null && capabilities.hasCapability(
                            NetworkCapabilities.NET_CAPABILITY_INTERNET);
                } catch (RuntimeException ignored) {
                    // Registration is still true; an eventual callback supplies network truth.
                }
            }
            try {
                if (abandoned && actuallyRegistered) {
                    try {
                        manager.unregisterNetworkCallback(callback);
                    } finally {
                        actuallyRegistered = false;
                    }
                }
                success = manager != null && actuallyRegistered;
            } catch (RuntimeException ignored) {
                success = false;
            }
            completion.complete(generation, success);
        });
    }

    boolean stopAndUnregister(long timeoutMillis) {
        boolean completed = lane.runAndWait(() -> {
            if (manager != null && actuallyRegistered) {
                try {
                    manager.unregisterNetworkCallback(callback);
                } catch (RuntimeException ignored) {
                } finally {
                    actuallyRegistered = false;
                }
            }
        }, timeoutMillis);
        lane.stopNow();
        lane.awaitStopped(timeoutMillis);
        return completed;
    }

    boolean unregister() {
        return lane.submit(() -> {
            if (manager != null && actuallyRegistered) {
                try {
                    manager.unregisterNetworkCallback(callback);
                } catch (RuntimeException ignored) {
                } finally {
                    actuallyRegistered = false;
                }
            }
        });
    }

    void abandon() {
        abandoned = true;
        lane.stopNow();
    }

    boolean isStopped() {
        return lane.isStopped();
    }
}
