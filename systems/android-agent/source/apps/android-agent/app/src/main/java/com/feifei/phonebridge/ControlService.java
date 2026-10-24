package com.feifei.phonebridge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.IBinder;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import org.json.JSONObject;

public final class ControlService extends Service {
    enum GuardianRecoveryStartResult {
        REQUESTED,
        REJECTED
    }

    static final String ACTION_PAUSE = "com.feifei.phonebridge.PAUSE";
    static final String ACTION_RESUME = "com.feifei.phonebridge.RESUME";
    static final String ACTION_STOP = "com.feifei.phonebridge.STOP";
    static final String ACTION_RECONNECT_CLOUD = "com.feifei.phonebridge.RECONNECT_CLOUD";
    static final String EXTRA_START_REASON = "com.feifei.phonebridge.START_REASON";
    static final String EXTRA_RECOVERY_GENERATION = "com.feifei.phonebridge.RECOVERY_GENERATION";
    static final String EXTRA_RECOVERY_PROCESS = "com.feifei.phonebridge.RECOVERY_PROCESS";
    static final String EXTRA_RECOVERY_OWNER = "com.feifei.phonebridge.RECOVERY_OWNER";
    private static final String CHANNEL_ID = "phonebridge_control";
    private static final String REMOVED_VOICE_CHANNEL_ID = "agent_voice_wake";
    private static final int NOTIFICATION_ID = 8765;
    private static final MonotonicSignalThrottle ACCESSIBILITY_WAKE_THROTTLE =
            new MonotonicSignalThrottle(2_000L);
    private static volatile boolean running;
    private static volatile boolean wakeLockHeld;
    private static volatile ControlService activeInstance;

    private volatile HttpBridgeServer server;
    private CloudChannelClient cloudChannel;
    private TaskMonitor taskMonitor;
    private RecoveryCoordinator recoveryCoordinator;
    private PowerManager.WakeLock cloudWakeLock;
    private final BoundedIoLane ancillaryIoLane =
            new BoundedIoLane("oin-service-ancillary-io", 8);
    private final BoundedIoLane diagnosticIoLane =
            new BoundedIoLane("oin-service-diagnostic-io", 8);
    private final long serviceStartedAtWallMillis = System.currentTimeMillis();
    private final Handler serviceWatchdogHandler = new Handler(Looper.getMainLooper());
    private final Runnable serviceWatchdogTick = new Runnable() {
        @Override
        public void run() {
            try {
                CloudChannelClient channel = cloudChannel;
                if (initialized && channel != null) {
                    channel.onServiceWatchdogTick();
                }
                diagnosticIoLane.submit(() -> CloudState.recordServiceWatchdogTick(
                        ControlService.this, SystemClock.elapsedRealtime()));
            } catch (RuntimeException watchdogFailure) {
                diagnosticIoLane.submit(() -> CloudState.recordConnectionError(
                        ControlService.this, "service_transport_watchdog_failed"));
            } finally {
                if (initialized) {
                    serviceWatchdogHandler.postDelayed(
                            this, ServiceTransportWatchdog.TICK_INTERVAL_MILLIS);
                }
            }
        }
    };
    private boolean initialized;

    @Override
    public void onCreate() {
        super.onCreate();
        // Fulfil Android's foreground-service deadline before preferences, diagnostics, process
        // exit Binder queries, local bridge files, or cloud transport bootstrap.
        try {
            createChannel();
            startForeground(NOTIFICATION_ID, buildStartupNotification(this));
            if (!AgentRuntimePreferences.autoConnectEnabled(this)) {
                running = false;
                stopForeground(STOP_FOREGROUND_REMOVE);
                stopSelf();
                return;
            }
            activeInstance = this;
            acquireCloudWakeLock();
            recoveryCoordinator = new RecoveryCoordinator(this);
            cloudChannel = new CloudChannelClient(this);
            cloudChannel.setRecoveryProgressListener(recoveryCoordinator::authenticatedProgress);
            cloudChannel.setRecoveryCoordinator(recoveryCoordinator);
            if (!recoveryCoordinator.attach(cloudChannel)) {
                throw new IllegalStateException("recovery_lease_unavailable");
            }
            String reconnectResult = cloudChannel.start();
            running = true;
            initialized = true;
            taskMonitor=new TaskMonitor(this);
            serviceWatchdogHandler.post(serviceWatchdogTick);
            startAncillaryRuntime(reconnectResult);
        } catch (RuntimeException initializationFailure) {
            running = false;
            diagnosticIoLane.submit(this::publishInitializationFailureSafely);
            stopRuntimeComponentsSafely();
            releaseCloudWakeLock();
            try {
                stopForeground(STOP_FOREGROUND_REMOVE);
            } catch (RuntimeException ignored) {
            } finally {
                if (activeInstance == this) {
                    activeInstance = null;
                }
                stopSelf();
            }
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (!AgentRuntimePreferences.autoConnectEnabled(this)) {
            running = false;
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        if (!initialized) {
            diagnosticIoLane.submit(() -> {
                CloudState.setServiceState(this, "service_initialization_failed");
                CloudState.recordReconnectResult(this, "service_initialization_failed");
            });
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        if(intent!=null&&TaskMonitor.ACTION.equals(intent.getAction())&&taskMonitor!=null){taskMonitor.control(intent);return START_STICKY;}
        String startReason = intent == null
                ? "sticky_service_recreated"
                : intent.getStringExtra(EXTRA_START_REASON);
        if (intent != null && intent.hasExtra(EXTRA_RECOVERY_GENERATION)) {
            recoveryCoordinator.resumeGuardianClaim(
                    intent.getLongExtra(EXTRA_RECOVERY_GENERATION, -1L),
                    intent.getStringExtra(EXTRA_RECOVERY_PROCESS),
                    intent.getStringExtra(EXTRA_RECOVERY_OWNER));
        } else {
            recoveryCoordinator.submitSignal(startReason == null || startReason.isEmpty()
                    ? "service_start_signal" : startReason);
        }
        if (startReason != null && !startReason.isEmpty()) {
            diagnosticIoLane.submit(() -> {
                CloudState.setServiceState(this, "service_running");
                AuditLog.append(this, startReason, true, null);
            });
        }
        if (intent != null && ACTION_RECONNECT_CLOUD.equals(intent.getAction())) {
            recoveryCoordinator.submitSignal("manual_reconnect");
            String result = "reconnect_scheduled";
            diagnosticIoLane.submit(() -> {
                CloudState.recordReconnectResult(this, result);
                refreshNotification(this);
            });
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if(taskMonitor!=null)taskMonitor.close();
        boolean wasInitialized = initialized;
        running = false;
        initialized = false;
        serviceWatchdogHandler.removeCallbacks(serviceWatchdogTick);
        if (activeInstance == this) {
            activeInstance = null;
        }
        try {
            stopRuntimeComponentsSafely();
            releaseCloudWakeLock();
            diagnosticIoLane.submit(() -> {
                try {
                if (wasInitialized) {
                    CloudState.setServiceState(this, "service_stopped");
                }
                AuditLog.append(this, "control_service_stopped", true, null);
                } catch (RuntimeException ignored) {
                    // Diagnostics must not prevent foreground resource cleanup.
                }
            });
            ancillaryIoLane.stopNow();
            diagnosticIoLane.stopNow();
        } finally {
            releaseCloudWakeLock();
            super.onDestroy();
        }
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        diagnosticIoLane.submit(() -> CloudState.recordConnectionError(
                this, "task_removed_recovery_requested"));
        // START_STICKY is the recovery contract. Android/OEM policy may still defer or suppress
        // recreation; this signal is diagnostic and never reported as an absolute guarantee.
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    static void refreshNotification(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.notify(NOTIFICATION_ID, buildNotification(context));
        }
    }

    static String requestStart(Context context) {
        return requestStart(context, "service_start_requested");
    }

    static String requestStart(Context context, String reason) {
        Context app = context.getApplicationContext();
        if (!AgentRuntimePreferences.autoConnectEnabled(app)) {
            return "auto_connect_disabled";
        }
        if (running) {
            return "service_running";
        }
        try {
            Intent start = new Intent(app, ControlService.class)
                    .putExtra(EXTRA_START_REASON, reason == null ? "" : reason);
            app.startForegroundService(start);
            return "service_start_requested";
        } catch (RuntimeException startFailure) {
            return "service_start_failed";
        }
    }

    static String requestRecovery(Context context, String reason) {
        if (!AgentRuntimePreferences.autoConnectEnabled(context)) {
            return "auto_connect_disabled";
        }
        RecoveryCoordinator coordinator = activeInstance == null
                ? null : activeInstance.recoveryCoordinator;
        if (coordinator != null) {
            coordinator.submitSignal(reason);
            return "recovery_signal_submitted";
        }
        return requestStart(context, reason);
    }

    static GuardianRecoveryStartResult requestGuardianRecovery(Context context, long generation,
            String processInstanceId, String guardianOwner) {
        Context app = context.getApplicationContext();
        try {
            Intent start = new Intent(app, ControlService.class)
                    .putExtra(EXTRA_START_REASON, "guardian_lease_claimed")
                    .putExtra(EXTRA_RECOVERY_GENERATION, generation)
                    .putExtra(EXTRA_RECOVERY_PROCESS, processInstanceId)
                    .putExtra(EXTRA_RECOVERY_OWNER, guardianOwner);
            app.startForegroundService(start);
            return GuardianRecoveryStartResult.REQUESTED;
        } catch (RuntimeException backgroundStartRejected) {
            return GuardianRecoveryStartResult.REJECTED;
        }
    }

    static String requestCloudReconnect(Context context) {
        if (!AgentRuntimePreferences.autoConnectEnabled(context)) {
            return "auto_connect_disabled";
        }
        RecoveryCoordinator coordinator = activeInstance == null
                ? null : activeInstance.recoveryCoordinator;
        if (coordinator != null) {
            coordinator.submitSignal("reconnect_request_dispatched");
            return "reconnect_request_dispatched";
        }
        Context app = context.getApplicationContext();
        Intent intent = new Intent(context, ControlService.class).setAction(ACTION_RECONNECT_CLOUD);
        try {
            intent.putExtra(EXTRA_START_REASON, "reconnect_request_dispatched");
            app.startForegroundService(intent);
            return "reconnect_request_dispatched";
        } catch (RuntimeException startFailure) {
            return "service_start_failed";
        }
    }

    static boolean isRunning() {
        return running;
    }

    static String setAutoConnectEnabled(Context context, boolean enabled) {
        Context app = context.getApplicationContext();
        if (!AgentRuntimePreferences.setAutoConnectEnabled(app, enabled)) {
            return "auto_connect_preference_failed";
        }
        if (enabled) {
            return requestStart(app, "auto_connect_enabled");
        }
        boolean stopped = app.stopService(new Intent(app, ControlService.class));
        return stopped || !running ? "auto_connect_disabled" : "service_stop_failed";
    }

    static String requestDeviceStateRefresh(Context context) {
        if (!AgentRuntimePreferences.autoConnectEnabled(context)) {
            return "auto_connect_disabled";
        }
        ControlService service = activeInstance;
        if (service != null && service.initialized && service.cloudChannel != null) {
            service.cloudChannel.onSystemActivitySignal();
            return "device_state_refresh_submitted";
        }
        return requestRecovery(context, "device_name_changed");
    }

    static boolean isWakeLockHeld() {
        return wakeLockHeld;
    }

    static boolean isCloudChannelFresh(Context context) {
        return running && CloudState.ONLINE_FRESH.equals(CloudState.connectionState(context)) &&
                ServerActivityWatchdog.isFreshWallClock(
                        CloudState.lastServerActivityAt(context),
                        System.currentTimeMillis(),
                        CloudChannelClient.SERVER_ACTIVITY_TIMEOUT_MILLIS
                );
    }

    static void notifyAccessibilityActivity(Context context) {
        long now = SystemClock.elapsedRealtime();
        if (!ACCESSIBILITY_WAKE_THROTTLE.tryAcquire(now)) {
            return;
        }
        ControlService service = activeInstance;
        if (service != null && service.initialized && service.cloudChannel != null) {
            service.recoveryCoordinator.submitSignal("accessibility_activity");
            return;
        }
        requestRecovery(context, "accessibility_activity");
    }

    private void acquireCloudWakeLock() {
        PowerManager powerManager = getSystemService(PowerManager.class);
        if (powerManager == null) {
            throw new IllegalStateException("power_manager_unavailable");
        }
        PowerManager.WakeLock wakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                getPackageName() + ":cloud-channel"
        );
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire();
        cloudWakeLock = wakeLock;
        wakeLockHeld = wakeLock.isHeld();
    }

    private void releaseCloudWakeLock() {
        PowerManager.WakeLock wakeLock = cloudWakeLock;
        cloudWakeLock = null;
        wakeLockHeld = false;
        try {
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
        } catch (RuntimeException ignored) {
        }
    }

    private void publishInitializationFailureSafely() {
        try {
            CloudState.setServiceState(this, "service_initialization_failed");
            CloudState.recordReconnectResult(this, "service_initialization_failed");
            AuditLog.append(this, "control_service_started", false, null);
        } catch (RuntimeException ignored) {
        }
    }

    private void startAncillaryRuntime(String reconnectResult) {
        ancillaryIoLane.submit(() -> {
            try {
                getSharedPreferences("voice_wake_state", MODE_PRIVATE).edit().clear().apply();
                BridgeState.initialize(this);
                if (BuildConfig.ENABLE_LOCAL_DIAGNOSTIC_BRIDGE && initialized) {
                    HttpBridgeServer localServer = new HttpBridgeServer(this);
                    localServer.start();
                    if (initialized) {
                        server = localServer;
                    } else {
                        localServer.stop();
                    }
                }
                if (!initialized) {
                    return;
                }
            } catch (RuntimeException ancillaryFailure) {
                diagnosticIoLane.submit(() -> {
                    try {
                        CloudState.recordConnectionError(this, "service_ancillary_io_failed");
                    } catch (RuntimeException ignored) {
                    }
                });
            }
        });
        diagnosticIoLane.submit(() -> {
            try {
                CloudState.recordServiceInstanceStarted(this, serviceStartedAtWallMillis);
                JSONObject previousExit = ProcessExitDiagnostics.previousExit(this);
                CloudState.recordProcessExitDiagnostic(this, previousExit.toString());
                if (!initialized) {
                    return;
                }
                CloudState.setServiceState(this, "service_running");
                CloudState.recordReconnectResult(this, reconnectResult);
                AuditLog.append(this, "control_service_started", true, null);
                refreshNotification(this);
            } catch (RuntimeException diagnosticFailure) {
                try {
                    CloudState.recordConnectionError(this, "service_diagnostic_io_failed");
                } catch (RuntimeException ignored) {
                }
            }
        });
    }

    private void stopRuntimeComponentsSafely() {
        serviceWatchdogHandler.removeCallbacks(serviceWatchdogTick);
        HttpBridgeServer localServer = server;
        server = null;
        if (localServer != null) {
            try {
                localServer.stop();
            } catch (RuntimeException ignored) {
            }
        }
        CloudChannelClient channel = cloudChannel;
        cloudChannel = null;
        if (channel != null) {
            try {
                channel.stop();
            } catch (RuntimeException ignored) {
            }
        }
        RecoveryCoordinator coordinator = recoveryCoordinator;
        recoveryCoordinator = null;
        if (coordinator != null) {
            try {
                coordinator.stop();
            } catch (RuntimeException ignored) {
            }
        }
    }

    private void createChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) {
            throw new IllegalStateException("notification_manager_unavailable");
        }
        manager.deleteNotificationChannel(REMOVED_VOICE_CHANNEL_ID);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "AgentOS 控制服务",
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("显示 AgentOS 控制状态和紧急暂停入口");
        manager.createNotificationChannel(channel);
    }

    private static Notification buildStartupNotification(Context context) {
        Intent open = new Intent(context, NativeTaskActivity.class);
        PendingIntent openIntent = PendingIntent.getActivity(
                context, 1, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("OIN 正在启动")
                .setContentText("正在恢复云端通道")
                .setOngoing(true)
                .setContentIntent(openIntent)
                .build();
    }

    private static Notification buildNotification(Context context) {
        boolean paused = BridgeState.PAUSED.get();
        String cloudState = CloudState.connectionState(context);
        if (CloudState.ONLINE_FRESH.equals(cloudState) && !isCloudChannelFresh(context)) {
            cloudState = CloudState.SUSPECT + " · heartbeat_stale";
        }
        Intent open = new Intent(context, NativeTaskActivity.class);
        PendingIntent openIntent = PendingIntent.getActivity(
                context, 1, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        String actionName = paused ? ACTION_RESUME : ACTION_PAUSE;
        String actionTitle = paused ? "恢复" : "立即暂停";
        Intent action = new Intent(context, ControlActionReceiver.class).setAction(actionName);
        PendingIntent actionIntent = PendingIntent.getBroadcast(
                context, 2, action, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        return new Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(paused ? "手机控制已暂停" : "OIN 手机连接已就绪")
                .setContentText("云端 " + cloudState + " · " +
                        (BuildConfig.ENABLE_LOCAL_DIAGNOSTIC_BRIDGE
                                ? "诊断 " + BuildConfig.LOCAL_DIAGNOSTIC_BIND_ADDRESS + ":" + BridgeState.PORT
                                : "本地诊断未启动"))
                .setOngoing(!paused)
                .setContentIntent(openIntent)
                .addAction(new Notification.Action.Builder(null, actionTitle, actionIntent).build())
                .build();
    }
}
