package com.feifei.phonebridge;

import android.content.Context;
import android.os.SystemClock;

import org.json.JSONObject;

import java.util.Arrays;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

final class CloudChannelClient {
    private interface ProtocolWork {
        void run() throws Exception;
    }
    private static final Set<String> ALLOWED_TASKS = new HashSet<>(Arrays.asList(
            "health",
            "observe",
            "xianyu_snapshot",
            "action"
    ));
    private static final Set<String> PRESERVED_SOCKET_CLOSE_REASONS =
            new HashSet<>(Arrays.asList(
                    "stale_client_activity",
                    "heartbeat_or_auth_timeout",
                    "replaced_by_new_device_session",
                    "authentication_or_protocol_rejected",
                    "task_channel_not_ready",
                    "task_result_outbox_capacity",
                    "task_store_unavailable"
            ));
    private static final long MAX_BACKOFF_MILLIS =
            CloudChannelTiming.MAX_RECONNECT_BACKOFF_MILLIS;
    // Client-side, server-to-device confirmation direction: a 30-second ACK/activity
    // gap is suspicious; probes continue until the 32-second retirement boundary.
    // The server's 90-second client-to-server inbound-silence timeout measures the opposite
    // direction and is intentionally not used as a numerical safety margin here.
    static final long SERVER_ACTIVITY_TIMEOUT_MILLIS =
            CloudChannelTiming.SUSPECT_AFTER_MILLIS;
    static final long SERVER_ACTIVITY_RETIRE_MILLIS =
            CloudChannelTiming.RETIRE_AFTER_MILLIS;
    private static final long CHANNEL_SUPERVISION_INTERVAL_SECONDS = 5L;
    private static final long AUTHENTICATION_TIMEOUT_MILLIS =
            CloudChannelTiming.AUTHENTICATION_TIMEOUT_MILLIS;
    private static final long ACTIVITY_HEARTBEAT_PROBE_INTERVAL_MILLIS = 5_000L;
    private static final long TASK_CHANNEL_RETRY_MAX_SECONDS = 60L;
    private static final long DEVICE_STATE_RETRY_MAX_SECONDS = 60L;
    private static final long DEVICE_STATE_ACK_TIMEOUT_SECONDS = 10L;
    private static final long DEVICE_STATE_REFRESH_SECONDS = 300L;
    private static final long NO_DEADLINE = -1L;
    private static final long MAX_SUPPORTED_HEARTBEAT_INTERVAL_SECONDS = 30L;
    private static final int DEVICE_REVOKED_CLOSE_CODE = 4403;
    private static final int DEVICE_IDENTITY_UNAVAILABLE_CLOSE_CODE = 4402;
    private static final int TASK_CHANNEL_NOT_READY_CLOSE_CODE = 4013;
    private static final int CLIENT_RECONNECT_CLOSE_CODE = 4007;
    private static final long OLD_SOCKET_CANCEL_GRACE_MILLIS = 2_000L;
    private static final long IDENTITY_IO_DEADLINE_MILLIS = 15_000L;
    private static final long SYSTEM_IO_DEADLINE_MILLIS = 10_000L;
    private static final long TASK_ADMISSION_DEADLINE_MILLIS = 7_000L;
    private static final int MAX_CONCURRENT_ABANDONED_IO_LANES = 2;

    private final Context context;
    private final OkHttpClient client;
    private final ScheduledExecutorService scheduler;
    private final TransportEventActor transportActor;
    private final TransportStatePublisher statePublisher;
    private BoundedIoLane identityIoLane;
    private final ScheduledExecutorService taskChannelScheduler;
    private final ScheduledExecutorService deviceStateScheduler;
    private final ExecutorService taskExecutor;
    private final ExecutorService livenessExecutor;
    private final TaskResultStore resultStore;
    private final CloudTaskExecutor executor;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AuthenticatedSocketGate<WebSocket> authenticatedSockets =
            new AuthenticatedSocketGate<>();
    private final ServerActivityWatchdog<WebSocket> serverActivityWatchdog =
            new ServerActivityWatchdog<>(
                    SERVER_ACTIVITY_TIMEOUT_MILLIS,
                    SERVER_ACTIVITY_RETIRE_MILLIS
            );
    private final TrustedServerClock serverClock = new TrustedServerClock();
    private final CloudConnectionLifecycle lifecycle =
            new CloudConnectionLifecycle(MAX_BACKOFF_MILLIS);
    private final RecoveryEpisode recoveryEpisode =
            new RecoveryEpisode(CloudChannelTiming.ORDINARY_AVAILABILITY_BUDGET_MILLIS);
    private final ConnectionAttemptGate connectionAttemptGate = new ConnectionAttemptGate();
    private final MonotonicSignalThrottle activityHeartbeatProbeThrottle =
            new MonotonicSignalThrottle(ACTIVITY_HEARTBEAT_PROBE_INTERVAL_MILLIS);
    private final ChannelLaneState channelLanes = new ChannelLaneState();
    private final ServiceTransportWatchdog serviceTransportWatchdog =
            new ServiceTransportWatchdog();
    private final AtomicBoolean activityHeartbeatProbeScheduled = new AtomicBoolean(false);
    private final AtomicBoolean reconciliationSendScheduled = new AtomicBoolean(false);
    private final Object lock = new Object();
    private final Object taskAdmissionLock = new Object();
    private final TaskAdmissionWatchdog taskAdmissionWatchdog = new TaskAdmissionWatchdog();
    private BoundedIoLane taskAdmissionIoLane = new BoundedIoLane("oin-task-admission-io", 2);
    private final NetworkCallbackRegistration networkRegistration =
            new NetworkCallbackRegistration();
    private NetworkCallbackIoLane networkCallbackIoLane;

    private WebSocket socket;
    private WebSocket challengedSocket;
    private ScheduledFuture<?> heartbeatFuture;
    private ScheduledFuture<?> supervisionFuture;
    private ScheduledFuture<?> taskRecoveryFuture;
    private volatile boolean usableNetwork;
    private boolean networkStateKnown;
    private boolean identityStateKnown;
    private boolean identityBound;
    private boolean identityRevoked;
    private String identityDeviceId;
    private String identityLastError;
    private long identityGeneration;
    private boolean identityPreflightInFlight;
    private long signatureGeneration;
    private final List<BoundedIoLane> abandonedIdentityLanes = new ArrayList<>();
    private final List<NetworkCallbackIoLane> abandonedNetworkLanes = new ArrayList<>();
    private boolean runtimeRestartRequested;
    private long connectionEpoch;
    private long socketInstalledAtElapsedMillis = NO_DEADLINE;
    private String supervisorRetireReason = "";
    private ReconciliationSession activeReconciliation;
    private int taskChannelRetryAttempt;
    private int deviceStateRetryAttempt;
    private boolean taskChannelWorkScheduled;
    private long taskChannelWorkEpoch = -1L;
    private boolean deviceStateWorkScheduled;
    private long deviceStateWorkEpoch = -1L;
    private long deviceStatePeriodicGeneration;
    private String pendingDeviceStateAck = "";
    private long pendingDeviceStateAckEpoch = -1L;
    private String pendingDeviceStatePayload = "";
    private boolean deviceStateRefreshRequested;
    private boolean currentHasRecoverySafeAfter;
    private long currentRecoverySafeAfter;
    private volatile Runnable recoveryProgressListener = () -> { };
    private volatile RecoveryCoordinator recoveryCoordinator;

    private static final class ReconciliationSession {
        final String authorizationId;
        final String challenge;
        final String oldScope;
        final String newScope;
        final long expiresAt;
        final List<TaskResultStore.ReconciliationRecord> records;

        ReconciliationSession(
                String authorizationId,
                String challenge,
                String oldScope,
                String newScope,
                long expiresAt,
                List<TaskResultStore.ReconciliationRecord> records
        ) {
            this.authorizationId = authorizationId;
            this.challenge = challenge;
            this.oldScope = oldScope;
            this.newScope = newScope;
            this.expiresAt = expiresAt;
            this.records = records;
        }

        TaskResultStore.ReconciliationRecord record(String taskId) {
            for (TaskResultStore.ReconciliationRecord record : records) {
                if (record.taskId.equals(taskId)) {
                    return record;
                }
            }
            return null;
        }

        boolean matches(String authorizationId, String oldScope, String newScope) {
            return this.authorizationId.equals(authorizationId) &&
                    this.oldScope.equals(oldScope) && this.newScope.equals(newScope);
        }
    }

    CloudChannelClient(Context context) {
        this.context = context.getApplicationContext();
        this.client = new OkHttpClient.Builder()
                .connectTimeout(CloudChannelTiming.CONNECT_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS)
                .readTimeout(0, TimeUnit.MILLISECONDS)
                .writeTimeout(CloudChannelTiming.CONNECT_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS)
                .pingInterval(30, TimeUnit.SECONDS)
                .retryOnConnectionFailure(true)
                .followRedirects(false)
                .followSslRedirects(false)
                .build();
        this.scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "oin-cloud-scheduler");
            thread.setDaemon(true);
            return thread;
        });
        this.transportActor = new TransportEventActor("oin-cloud-transport-actor");
        this.statePublisher = new TransportStatePublisher(this.context);
        this.identityIoLane = new BoundedIoLane("oin-identity-io", 4);
        this.taskChannelScheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "oin-task-channel");
            thread.setDaemon(true);
            return thread;
        });
        this.deviceStateScheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "oin-device-state");
            thread.setDaemon(true);
            return thread;
        });
        this.taskExecutor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "oin-cloud-task");
            thread.setDaemon(true);
            return thread;
        });
        this.livenessExecutor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "oin-cloud-liveness");
            thread.setDaemon(true);
            return thread;
        });
        this.resultStore = TaskResultStore.getProcessInstance(this.context);
        this.executor = new CloudTaskExecutor(this.context);
        this.networkCallbackIoLane = new NetworkCallbackIoLane(
                this.context, this::onNetworkCallbackSignal);
        // Construction is deliberately pure object setup. Binder, SharedPreferences and
        // Keystore truth arrive asynchronously through their dedicated I/O lanes.
        this.usableNetwork = false;
        this.networkStateKnown = false;
        this.identityDeviceId = "";
        this.identityBound = false;
        this.identityRevoked = false;
        this.identityLastError = "";
        this.identityStateKnown = false;
    }

    String start() {
        if (!running.compareAndSet(false, true)) {
            return reconnectNow();
        }
        postTransportEvent(() -> {
            startSupervisor();
            registerNetworkCallback();
            beginReconnect(false);
        });
        return "reconnect_scheduled";
    }

    void setRecoveryProgressListener(Runnable listener) {
        recoveryProgressListener = listener == null ? () -> { } : listener;
    }

    void setRecoveryCoordinator(RecoveryCoordinator coordinator) {
        recoveryCoordinator = coordinator;
    }

    void onRecoverySignal(String reason) {
        if (!running.get()) {
            return;
        }
        postTransportEvent(() -> {
            requireTransportActor();
            WebSocket current = currentSocket();
            if (current != null && isAuthenticated(current)) {
                sendHeartbeat(current);
            } else {
                beginReconnect(true);
            }
        });
    }

    void onInfrastructureRecoverySignal(String reason) {
        if (!running.get()) {
            return;
        }
        postTransportEvent(() -> handleInfrastructureRecoverySignal(reason));
    }

    private void handleInfrastructureRecoverySignal(String reason) {
        requireTransportActor();
        if (!running.get() || identityRevoked) {
            return;
        }
        // The cross-process lease permits one bounded fresh lane per process generation. It does
        // not clear abandoned lanes or create an unbounded thread-replacement loop.
        if ("identity_io_rotation_exhausted".equals(reason) && identityIoLane == null) {
            identityIoLane = new BoundedIoLane(
                    "oin-identity-io-guardian-r" + identityGeneration, 4);
        } else if ("network_io_rotation_exhausted".equals(reason) &&
                networkCallbackIoLane == null) {
            networkCallbackIoLane = new NetworkCallbackIoLane(
                    context, this::onNetworkCallbackSignal);
        }
        runtimeRestartRequested = false;
        registerNetworkCallback();
        beginReconnect(true);
    }

    String reconnectNow() {
        if (!running.get()) {
            return "service_not_running";
        }
        postTransportEvent(() -> beginReconnect(true));
        return "reconnect_scheduled";
    }

    void onSystemActivitySignal() {
        postTransportEvent(this::handleSystemActivitySignal);
    }

    private void handleSystemActivitySignal() {
        requireTransportActor();
        if (!running.get() || identityRevoked) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        WebSocket current;
        boolean authenticated;
        CloudConnectionLifecycle.Snapshot reconnect = null;
        synchronized (lock) {
            current = socket;
            authenticated = current != null && authenticatedSockets.isAuthenticated(current);
            if (current == null) {
                reconnect = prepareImmediateAndWaitLocked(now, "accessibility_activity_reconcile");
            }
        }
        if (reconnect != null) {
            publishLifecycle(reconnect, now);
        }
        publishDiagnostic(() -> CloudState.recordSupervisorTick(
                context, now, current == null ? now : NO_DEADLINE,
                authenticated ? "accessibility_activity_online" : "accessibility_activity_reconcile"));
        if (authenticated && activityHeartbeatProbeThrottle.tryAcquire(now)) {
            scheduleActivityHeartbeatProbe();
        }
        if (authenticated) {
            scheduleDeviceStateRefresh(current, 0L);
        }
        wakeSupervisor();
    }

    void onServiceWatchdogTick() {
        postTransportEvent(this::handleServiceWatchdogTick);
    }

    private void handleServiceWatchdogTick() {
        requireTransportActor();
        if (!running.get() || identityRevoked) {
            return;
        }
        // Service and accessibility callbacks are wake signals only. Transport ownership remains
        // on the actor so an old callback cannot mutate a replacement epoch.
        runSupervisorSafely();
        long now = SystemClock.elapsedRealtime();
        WebSocket current;
        boolean authenticated;
        synchronized (lock) {
            current = socket;
            authenticated = current != null &&
                    authenticatedSockets.isAuthenticated(current);
        }
        if (current != null &&
                serviceTransportWatchdog.claimHeartbeatRescue(now, authenticated)) {
            sendHeartbeat(current);
        }
    }

    private void scheduleActivityHeartbeatProbe() {
        if (!activityHeartbeatProbeScheduled.compareAndSet(false, true)) {
            return;
        }
        try {
            livenessExecutor.execute(() -> postTransportEvent(() -> {
                try {
                    WebSocket current = currentSocket();
                    if (current != null && isAuthenticated(current) && running.get()) {
                        sendHeartbeat(current);
                    }
                } finally {
                    activityHeartbeatProbeScheduled.set(false);
                }
            }));
        } catch (RejectedExecutionException executorStopped) {
            activityHeartbeatProbeScheduled.set(false);
            publishDiagnostic(() -> CloudState.recordConnectionError(
                    context, "cloud_liveness_probe_unavailable"));
            postTransportEvent(() -> requestSupervisorSocketRetirement(
                    "cloud_liveness_probe_unavailable"));
        }
    }

    private String beginReconnect(boolean replacePendingAttempt) {
        requireTransportActor();
        if (identityPreflightInFlight) {
            return recordReconnect("identity_preflight_in_progress");
        }
        identityPreflightInFlight = true;
        final long requestedGeneration = ++identityGeneration;
        final long requestedEpoch = connectionEpoch;
        ensureIdentityLaneCapacity();
        final BoundedIoLane requestedLane = identityIoLane;
        if (requestedLane == null) {
            scheduleIdentityRetry("identity_io_capacity_wait");
            return recordReconnect("identity_io_capacity_wait");
        }
        boolean accepted = requestedLane.submit(() -> {
            IdentitySnapshot snapshot = loadIdentitySnapshot();
            postTransportEvent(() -> completeIdentityPreflight(
                    requestedGeneration,
                    requestedEpoch,
                    replacePendingAttempt,
                    snapshot));
        });
        if (!accepted) {
            identityPreflightInFlight = false;
            identityStateKnown = false;
            rotateIdentityLane(requestedLane);
            scheduleIdentityRetry("identity_io_unavailable");
            return recordReconnect("identity_io_unavailable");
        }
        transportActor.schedule(
                () -> expireIdentityPreflight(requestedGeneration, requestedLane),
                IDENTITY_IO_DEADLINE_MILLIS,
                TimeUnit.MILLISECONDS);
        return recordReconnect("identity_preflight_scheduled");
    }

    private void expireIdentityPreflight(long generation, BoundedIoLane lane) {
        requireTransportActor();
        if (generation != identityGeneration || !identityPreflightInFlight) {
            return;
        }
        identityPreflightInFlight = false;
        identityStateKnown = false;
        identityGeneration++;
        rotateIdentityLane(lane);
        scheduleIdentityRetry("identity_preflight_timeout");
    }

    private void completeIdentityPreflight(
            long completedGeneration,
            long requestedEpoch,
            boolean replacePendingAttempt,
            IdentitySnapshot snapshot
    ) {
        requireTransportActor();
        if (completedGeneration != identityGeneration) {
            return;
        }
        identityPreflightInFlight = false;
        if (requestedEpoch != connectionEpoch) {
            beginReconnect(false);
            return;
        }
        identityBound = snapshot.bound;
        identityRevoked = snapshot.revoked;
        identityDeviceId = snapshot.deviceId;
        identityLastError = snapshot.lastErrorCode;
        if (!snapshot.recoverySucceeded) {
            identityStateKnown = false;
            applyReconnectFailure("credential_activation_persistence_failed");
            scheduleIdentityRetry("credential_activation_persistence_failed");
            return;
        }
        identityStateKnown = true;
        continueReconnect(replacePendingAttempt);
    }

    private void continueReconnect(boolean replacePendingAttempt) {
        requireTransportActor();
        String preflight = ReconnectPreflight.evaluate(
                running.get(),
                identityRevoked,
                CloudConfig.isConfigured(),
                identityBound,
                hasUsableNetwork()
        );
        if (!networkStateKnown) {
            registerNetworkCallback();
            recordReconnect("network_observation_pending");
            return;
        }
        if (!ReconnectPreflight.READY.equals(preflight)) {
            applyReconnectFailure(preflight);
            if (ReconnectPreflight.NETWORK_UNAVAILABLE.equals(preflight)) {
                registerNetworkCallback();
                if (currentSocket() == null) {
                    scheduleBackoff("network_unavailable");
                } else {
                    requestSupervisorSocketRetirement("network_unavailable");
                }
            } else if (!ReconnectPreflight.SERVICE_NOT_RUNNING.equals(preflight)) {
                closeCurrent(preflight);
            }
            recordReconnect(preflight);
            return;
        }

        registerNetworkCallback();
        if (replacePendingAttempt) {
            requestSupervisorSocketRetirement("manual_reconnect");
        } else {
            requestSupervisorReconnectNow("service_started");
        }
        recordReconnect("reconnect_scheduled");
    }

    private void applyReconnectFailure(String code) {
        switch (code) {
            case ReconnectPreflight.DEVICE_REVOKED:
                identityRevoked = true;
                blockRecoveryEpisode(SystemClock.elapsedRealtime(), "device_revoked");
                publishDiagnostic(() -> CloudState.markRevoked(context));
                break;
            case ReconnectPreflight.CONFIG_REQUIRED:
                blockRecoveryEpisode(SystemClock.elapsedRealtime(), "config_required");
                publishDiagnostic(() -> CloudState.setConnectionState(
                        context, CloudState.CONFIG_REQUIRED, code));
                break;
            case ReconnectPreflight.DEVICE_UNBOUND:
                identityBound = false;
                identityDeviceId = "";
                blockRecoveryEpisode(SystemClock.elapsedRealtime(), "device_unbound");
                publishDiagnostic(() -> CloudState.setConnectionState(
                        context, CloudState.UNBOUND, code));
                break;
            case ReconnectPreflight.NETWORK_UNAVAILABLE:
            case ReconnectPreflight.SERVICE_NOT_RUNNING:
                publishLifecycle(lifecycle.markBoundOffline(code), SystemClock.elapsedRealtime());
                break;
            default:
                publishDiagnostic(() -> CloudState.recordConnectionError(context, code));
                break;
        }
    }

    private String recordReconnect(String code) {
        publishDiagnostic(() -> CloudState.recordReconnectResult(context, code));
        return code;
    }

    private void requireTransportActor() {
        transportActor.requireActorThread();
    }

    private void postTransportEvent(Runnable event) {
        try {
            transportActor.post(event);
        } catch (RejectedExecutionException stopped) {
            if (running.get()) {
                publishDiagnostic(() -> CloudState.recordConnectionError(
                        context, "transport_actor_unavailable"));
            }
        }
    }

    private void publishDiagnostic(Runnable publication) {
        statePublisher.execute(publication);
    }

    private void dispatchProtocolWork(
            ExecutorService lane,
            WebSocket current,
            ProtocolWork work
    ) {
        try {
            lane.execute(() -> {
                try {
                    work.run();
                } catch (Exception failure) {
                    postTransportEvent(() -> handleProtocolFailure(current, failure));
                }
            });
        } catch (RejectedExecutionException stopped) {
            handleProtocolFailure(current, new IllegalStateException("protocol_lane_unavailable"));
        }
    }

    private void handleProtocolFailure(WebSocket current, Exception protocolFailure) {
        requireTransportActor();
        final String failureCode;
        synchronized (lock) {
            if (socket != current) {
                current.cancel();
                return;
            }
            failureCode = protocolFailure instanceof DeviceIdentity.DeviceIdentityException
                    ? ((DeviceIdentity.DeviceIdentityException) protocolFailure).code
                    : "cloud_protocol_rejected";
        }
        publishDiagnostic(() -> CloudState.recordConnectionError(context, failureCode));
        requestSupervisorSocketRetirement(current, failureCode);
        if (protocolFailure instanceof DeviceIdentity.DeviceIdentityException) {
            current.close(
                    DEVICE_IDENTITY_UNAVAILABLE_CLOSE_CODE,
                    "device_identity_unavailable");
        } else {
            current.close(4002, "protocol_rejected");
        }
    }

    private void publishLifecycle(
            CloudConnectionLifecycle.Snapshot snapshot,
            long nowElapsedMillis
    ) {
        requireTransportActor();
        String errorCode = null;
        if (CloudConnectionLifecycle.SUSPECT.equals(snapshot.state) ||
                CloudConnectionLifecycle.RECONNECT_WAIT.equals(snapshot.state) ||
                CloudConnectionLifecycle.BOUND_OFFLINE.equals(snapshot.state)) {
            errorCode = snapshot.reason;
        }
        final String publishedError = errorCode;
        final long epoch = connectionEpoch;
        statePublisher.submit(publishedContext -> {
            CloudState.setConnectionState(
                    publishedContext, snapshot.state, publishedError, false);
            CloudState.recordReconnectPlan(
                    publishedContext, nowElapsedMillis, snapshot, epoch);
            ControlService.refreshNotification(publishedContext);
        });
    }

    private void startRecoveryEpisode(long nowElapsedMillis, String trigger) {
        startRecoveryEpisode(
                nowElapsedMillis,
                trigger,
                CloudChannelTiming.OBSERVED_DISCONNECT_RECOVERY_BUDGET_MILLIS);
    }

    private void startRecoveryEpisode(
            long startElapsedMillis,
            String trigger,
            long budgetMillis
    ) {
        requireTransportActor();
        RecoveryEpisode.Snapshot before = recoveryEpisode.snapshot();
        RecoveryEpisode.Snapshot episode = recoveryEpisode.startIfIdle(
                startElapsedMillis, trigger, budgetMillis);
        if (episode.generation != before.generation ||
                !episode.outcome.equals(before.outcome)) {
            publishRecoveryEpisode(episode, SystemClock.elapsedRealtime());
        }
    }

    private void recordRecoveryAttempt(long nowElapsedMillis) {
        requireTransportActor();
        if (!recoveryEpisode.isInProgress()) {
            return;
        }
        publishRecoveryEpisode(recoveryEpisode.recordAttempt(), nowElapsedMillis);
    }

    private void recordRecoveryFailure(long nowElapsedMillis, String failureStage) {
        requireTransportActor();
        if (!recoveryEpisode.isInProgress()) {
            startRecoveryEpisode(nowElapsedMillis, "transport_failure");
        }
        publishRecoveryEpisode(recoveryEpisode.recordFailure(failureStage), nowElapsedMillis);
    }

    private void completeRecoveryEpisode(long nowElapsedMillis) {
        requireTransportActor();
        if (recoveryEpisode.isInProgress()) {
            publishRecoveryEpisode(recoveryEpisode.recovered(nowElapsedMillis), nowElapsedMillis);
        }
        recoveryEpisode.allowNextEpisode();
        recoveryProgressListener.run();
    }

    private void blockRecoveryEpisode(long nowElapsedMillis, String failureStage) {
        requireTransportActor();
        if (recoveryEpisode.isInProgress()) {
            publishRecoveryEpisode(
                    recoveryEpisode.blocked(nowElapsedMillis, failureStage), nowElapsedMillis);
        }
        if ("device_revoked".equals(failureStage) || "device_unbound".equals(failureStage) ||
                "config_required".equals(failureStage)) {
            RecoveryCoordinator coordinator = recoveryCoordinator;
            if (coordinator != null) {
                coordinator.blockTerminal();
            }
        }
    }

    private void enforceRecoveryBudget(long nowElapsedMillis) {
        requireTransportActor();
        if (!recoveryEpisode.isDue(nowElapsedMillis)) {
            return;
        }
        RecoveryEpisode.Snapshot expired = recoveryEpisode.expireIfDue(nowElapsedMillis);
        publishRecoveryEpisode(expired, nowElapsedMillis);
        publishDiagnostic(() -> CloudState.recordConnectionError(
                context, "recovery_budget_exhausted"));
    }

    private void publishRecoveryEpisode(
            RecoveryEpisode.Snapshot episode,
            long nowElapsedMillis
    ) {
        final long wallNow = System.currentTimeMillis();
        statePublisher.submit(publishedContext -> {
            CloudState.recordRecoveryEpisode(publishedContext, episode, nowElapsedMillis, wallNow);
        });
    }

    private CloudConnectionLifecycle.Snapshot prepareBackoffAndWaitLocked(
            long nowElapsedMillis,
            String reason
    ) {
        long jitterMillis = (System.nanoTime() & 1L) * 1000L;
        lifecycle.prepareBackoffReconnect(nowElapsedMillis, jitterMillis, reason);
        return lifecycle.enterReconnectWait(reason);
    }

    private CloudConnectionLifecycle.Snapshot prepareImmediateAndWaitLocked(
            long nowElapsedMillis,
            String reason
    ) {
        lifecycle.prepareImmediateReconnect(nowElapsedMillis, reason);
        return lifecycle.enterReconnectWait(reason);
    }

    void stop() {
        if (!running.compareAndSet(true, false)) {
            return;
        }
        FailSafeStopCoordinator cleanup = new FailSafeStopCoordinator();
        cleanup.run(() ->
            transportActor.postAndWait(() -> {
                requireTransportActor();
                cancelFuture(heartbeatFuture);
                cancelFuture(taskRecoveryFuture);
                heartbeatFuture = null;
                taskRecoveryFuture = null;
                socketInstalledAtElapsedMillis = NO_DEADLINE;
                supervisorRetireReason = "";
                lifecycle.markBoundOffline("service_stopped");
                connectionAttemptGate.clear();
                cancelFuture(supervisionFuture);
                supervisionFuture = null;
                closeCurrent("service_stopped");
                networkRegistration.stopped();
            }, 2_500L));
        cleanup.run(transportActor::shutdownNow);
        // Actor timeout cannot leave an OkHttp call/socket owned by this client alive until the
        // server lease expires. This client instance is not shared with a replacement Service.
        cleanup.run(() -> client.dispatcher().cancelAll());
        cleanup.run(() -> client.connectionPool().evictAll());
        cleanup.run(() -> {
            if (networkCallbackIoLane != null) {
                networkCallbackIoLane.stopAndUnregister(2_500L);
            }
            for (NetworkCallbackIoLane abandoned : abandonedNetworkLanes) {
                abandoned.stopAndUnregister(250L);
            }
        });
        cleanup.run(() -> {
            if (identityIoLane != null) {
                identityIoLane.stopNow();
            }
            for (BoundedIoLane abandoned : abandonedIdentityLanes) {
                abandoned.stopNow();
            }
        });
        cleanup.run(() -> {
            if (identityIoLane != null) {
                identityIoLane.awaitStopped(2_500L);
            }
        });
        cleanup.run(scheduler::shutdownNow);
        cleanup.run(taskChannelScheduler::shutdownNow);
        cleanup.run(() -> {
            if (taskAdmissionIoLane != null) {
                taskAdmissionIoLane.stopNow();
            }
        });
        cleanup.run(deviceStateScheduler::shutdownNow);
        cleanup.run(livenessExecutor::shutdownNow);
        cleanup.run(() -> {
            taskExecutor.shutdownNow();
            boolean taskExecutorStopped = false;
            try {
                taskExecutorStopped = taskExecutor.awaitTermination(2500L, TimeUnit.MILLISECONDS);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
            if (TaskStoreRules.canRecoverAfterExecutorStop(
                    taskExecutorStopped,
                    resultStore.isTaskChannelReady()
            )) {
                try {
                    resultStore.recoverInterrupted();
                    AuditLog.append(context, "task_executor_stopped_and_recovered", true, null);
                } catch (RuntimeException recoveryFailure) {
                    handleResultStoreFailure(null, recoveryFailure);
                }
            } else if (!taskExecutorStopped) {
                AuditLog.append(context, "task_executor_shutdown_timeout", false, null);
            }
        });
        cleanup.run(() -> {
            boolean published = statePublisher.executeAndWait(() -> {
                CloudState.setConnectionState(
                        context, CloudState.BOUND_OFFLINE, "service_stopped", false);
                CloudState.recordReconnectResult(context, "service_stopped");
            }, 1_500L);
            if (!published) {
                // Fail-closed terminal persistence is deliberately outside the actor and every
                // publisher queue. It may be slower during shutdown, but cannot be silently lost.
                CloudState.setConnectionState(
                        context, CloudState.BOUND_OFFLINE, "service_stopped", false);
                CloudState.recordReconnectResult(context, "service_stopped");
            }
        });
        cleanup.run(statePublisher::stop);
        if (cleanup.failures() > 0) {
            try {
                CloudState.recordConnectionError(context, "service_cleanup_incomplete");
            } catch (RuntimeException ignored) {
            }
        }
    }

    private void connect(long attemptEpoch) {
        requireTransportActor();
        try {
            connectAttempt(attemptEpoch);
        } catch (RuntimeException failure) {
            connectionAttemptGate.finish(attemptEpoch);
            throw failure;
        }
    }

    private void connectAttempt(long attemptEpoch) {
        requireTransportActor();
        if (!running.get() || !identityBound || identityRevoked ||
                !CloudConfig.isConfigured()) {
            connectionAttemptGate.finish(attemptEpoch);
            return;
        }
        if (!hasUsableNetwork()) {
            connectionAttemptGate.finish(attemptEpoch);
            publishDiagnostic(() -> {
                CloudState.recordConnectionError(context, "network_unavailable");
                CloudState.recordReconnectResult(context, "network_unavailable");
            });
            scheduleBackoff("network_unavailable");
            return;
        }
        synchronized (lock) {
            if (socket != null || connectionEpoch != attemptEpoch) {
                connectionAttemptGate.finish(attemptEpoch);
                return;
            }
        }
        publishLifecycle(lifecycle.markConnecting(), SystemClock.elapsedRealtime());
        Request request = new Request.Builder().url(CloudConfig.websocketEndpoint()).build();
        WebSocket created = client.newWebSocket(request, new Listener(attemptEpoch));
        boolean installed = installSocketForEpoch(created, attemptEpoch);
        if (!installed) {
            created.cancel();
        }
    }

    private IdentitySnapshot loadIdentitySnapshot() {
        boolean recovered = true;
        try {
            if (CloudState.hasPendingIdentityActivation(context)) {
                String aliasId = CloudState.pendingIdentityActivationAlias(context);
                boolean activated = DeviceIdentity.recoverPreparedActivation(context, aliasId);
                recovered = activated && CloudState.clearPendingIdentityActivation(context);
                if (!recovered) {
                    CloudState.recordConnectionError(
                            context, "credential_activation_persistence_failed");
                }
            }
        } catch (DeviceIdentity.DeviceIdentityException failure) {
            recovered = false;
            CloudState.recordConnectionError(context, failure.code);
        }
        String deviceId = CloudState.deviceId(context);
        return new IdentitySnapshot(
                recovered,
                !deviceId.isEmpty(),
                CloudState.isRevoked(context),
                deviceId,
                CloudState.lastErrorCode(context));
    }

    private static final class IdentitySnapshot {
        final boolean recoverySucceeded;
        final boolean bound;
        final boolean revoked;
        final String deviceId;
        final String lastErrorCode;

        IdentitySnapshot(
                boolean recoverySucceeded,
                boolean bound,
                boolean revoked,
                String deviceId,
                String lastErrorCode
        ) {
            this.recoverySucceeded = recoverySucceeded;
            this.bound = bound;
            this.revoked = revoked;
            this.deviceId = deviceId == null ? "" : deviceId;
            this.lastErrorCode = lastErrorCode == null ? "" : lastErrorCode;
        }
    }

    private boolean installSocketForEpoch(WebSocket candidate, long attemptEpoch) {
        requireTransportActor();
        long installedEpoch = -1L;
        synchronized (lock) {
            if (!running.get() || connectionEpoch != attemptEpoch ||
                    (socket != null && socket != candidate)) {
                connectionAttemptGate.finish(attemptEpoch);
                return false;
            }
            if (socket == null) {
                socket = candidate;
                challengedSocket = null;
                socketInstalledAtElapsedMillis = SystemClock.elapsedRealtime();
                authenticatedSockets.clearAll();
                serverActivityWatchdog.clearAll();
                installedEpoch = connectionEpoch;
            }
            connectionAttemptGate.finish(attemptEpoch);
        }
        if (installedEpoch >= 0L) {
            final long publishedEpoch = installedEpoch;
            publishDiagnostic(() -> CloudState.recordSocketEpoch(context, publishedEpoch));
        }
        return true;
    }

    private void startSupervisor() {
        requireTransportActor();
        try {
            supervisionFuture = transportActor.scheduleWithFixedDelay(
                    this::runSupervisorSafely,
                    0L,
                    CHANNEL_SUPERVISION_INTERVAL_SECONDS,
                    TimeUnit.SECONDS
            );
        } catch (RejectedExecutionException schedulerStopped) {
            publishDiagnostic(() -> CloudState.recordConnectionError(
                    context, "cloud_supervisor_unavailable"));
        }
    }

    private void runSupervisorSafely() {
        requireTransportActor();
        SupervisorIterationGuard.run(this::superviseChannel, () -> {
            connectionAttemptGate.clear();
            publishDiagnostic(() -> {
                CloudState.recordConnectionError(context, "cloud_supervisor_iteration_failed");
                AuditLog.append(context, "cloud_supervisor_iteration_failed", false, null);
            });
            scheduleBackoff("supervisor_iteration_failed");
        });
    }

    private void superviseChannel() {
        requireTransportActor();
        long now = SystemClock.elapsedRealtime();
        enforceRecoveryBudget(now);
        if (!running.get()) {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, NO_DEADLINE, "service_not_running"));
            return;
        }
        // These two recovery sources progress independently. An unknown/failed identity must not
        // suppress network observation, and a missing network callback must not suppress identity
        // refresh. Only socket creation waits for both facts.
        if (!networkRegistration.isRegistered()) {
            registerNetworkCallback();
        }
        if (!identityStateKnown && !identityPreflightInFlight) {
            beginReconnect(false);
        }
        WebSocket current;
        boolean authenticated;
        long installedAt;
        long retryAt;
        long observedEpoch;
        String retireReason;
        synchronized (lock) {
            current = socket;
            authenticated = current != null && authenticatedSockets.isAuthenticated(current);
            installedAt = socketInstalledAtElapsedMillis;
            retryAt = lifecycle.retryAtElapsedMillis();
            observedEpoch = connectionEpoch;
            retireReason = supervisorRetireReason;
        }
        if (current != null && !retireReason.isEmpty()) {
            retireSocket(current, retireReason);
            return;
        }
        if (current != null && authenticated) {
            ServerActivityWatchdog.Observation<WebSocket> observation =
                    serverActivityWatchdog.observe(current, now);
            if (observation.health == ServerActivityWatchdog.Health.RETIRE_DUE) {
                retireSocket(current, "heartbeat_ack_grace_exhausted");
                return;
            }
            if (observation.health == ServerActivityWatchdog.Health.SUSPECT) {
                long lastActivity = observation.retireDeadlineElapsedMillis -
                        CloudChannelTiming.RETIRE_AFTER_MILLIS;
                CloudConnectionLifecycle.Snapshot suspect = null;
                synchronized (lock) {
                    // Revalidate socket ownership, epoch, authentication, and the watchdog
                    // activity generation in one critical section with the state transition.
                    // An ACK between observe() and this fence increments the generation and
                    // prevents a stale SUSPECT from overwriting ONLINE_FRESH.
                    if (socket == current && connectionEpoch == observedEpoch &&
                            authenticatedSockets.isAuthenticated(current) &&
                            serverActivityWatchdog.isCurrent(observation)) {
                        suspect = lifecycle.markSuspect("heartbeat_ack_suspect");
                        // Publish before releasing the same socket/epoch fence. Otherwise an
                        // ACK could publish ONLINE_FRESH and then be overwritten by this stale
                        // supervisor snapshot after the lock was released.
                        publishLifecycle(suspect, now);
                    }
                }
                if (suspect == null) {
                    return;
                }
                startRecoveryEpisode(
                        lastActivity,
                        "heartbeat_ack_suspect",
                        CloudChannelTiming.ORDINARY_AVAILABILITY_BUDGET_MILLIS);
                if (activityHeartbeatProbeThrottle.tryAcquire(now)) {
                    scheduleActivityHeartbeatProbe();
                }
                publishDiagnostic(() -> CloudState.recordSupervisorTick(
                        context, now, observation.retireDeadlineElapsedMillis,
                        "heartbeat_probe_grace"));
                return;
            }
        }
        if (current != null && !authenticated && installedAt != NO_DEADLINE &&
                now - installedAt >= AUTHENTICATION_TIMEOUT_MILLIS) {
            recordRecoveryFailure(now, "authentication");
            retireSocket(current, "cloud_auth_timeout");
            return;
        }
        if (current != null) {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, NO_DEADLINE,
                    authenticated ? "authenticated" : "authenticating"));
            return;
        }
        if (!identityStateKnown || identityPreflightInFlight || !networkStateKnown) {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, NO_DEADLINE, "startup_preflight_pending"));
            return;
        }
        if (identityRevoked || !identityBound ||
                !CloudConfig.isConfigured()) {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, NO_DEADLINE, "reconnect_preflight_blocked"));
            return;
        }
        if (retryAt != NO_DEADLINE && now < retryAt) {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, retryAt, "backoff_wait"));
            return;
        }
        final long attemptEpoch;
        boolean attemptInProgress = false;
        synchronized (lock) {
            if (socket != null) {
                return;
            }
            supervisorRetireReason = "";
            if (!lifecycle.claimReconnectIfDue(now)) {
                if (lifecycle.retryAtElapsedMillis() == NO_DEADLINE) {
                    scheduleBackoff("reconnect_state_reconciled");
                }
                return;
            }
            attemptEpoch = connectionEpoch;
            if (!connectionAttemptGate.tryStart(attemptEpoch)) {
                attemptInProgress = true;
            }
        }
        if (attemptInProgress) {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, NO_DEADLINE, "connect_attempt_in_progress"));
            return;
        }
        publishLifecycle(lifecycle.snapshot(), now);
        recordRecoveryAttempt(now);
        publishDiagnostic(() -> CloudState.recordSupervisorTick(
                context, now, NO_DEADLINE, "connect_attempt"));
        connect(attemptEpoch);
    }

    private void scheduleBackoff() {
        scheduleBackoff("cloud_disconnected");
    }

    private void scheduleBackoff(String reason) {
        requireTransportActor();
        if (!running.get() || identityRevoked) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        CloudConnectionLifecycle.Snapshot reconnect;
        synchronized (lock) {
            if (socket != null) {
                return;
            }
            reconnect = prepareBackoffAndWaitLocked(now, reason);
        }
        publishLifecycle(reconnect, now);
        try {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, reconnect.retryAtElapsedMillis, reason));
        } finally {
            wakeSupervisor();
        }
    }

    private void startHeartbeat(WebSocket current, long intervalSeconds) {
        requireTransportActor();
        boolean schedulerUnavailable = false;
        long heartbeatEpoch = -1L;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current) ||
                    !running.get()) {
                return;
            }
            cancelFuture(heartbeatFuture);
            try {
                heartbeatFuture = scheduler.scheduleAtFixedRate(
                        () -> postTransportEvent(() -> sendHeartbeat(current)),
                        intervalSeconds,
                        intervalSeconds,
                        TimeUnit.SECONDS
                );
            } catch (RejectedExecutionException rejected) {
                heartbeatFuture = null;
                schedulerUnavailable = true;
                heartbeatEpoch = connectionEpoch;
            }
        }
        if (schedulerUnavailable) {
            if (recordTransportFailureForCurrent(
                    current,
                    heartbeatEpoch,
                    "heartbeat_scheduler",
                    "heartbeat_scheduler")) {
                requestSupervisorSocketRetirement(current, "heartbeat_failed");
            }
            return;
        }
        sendHeartbeat(current);
    }

    private void retireSocket(WebSocket current, String reason) {
        requireTransportActor();
        boolean retired = false;
        long now = SystemClock.elapsedRealtime();
        long retiredEpoch = -1L;
        CloudConnectionLifecycle.Snapshot reconnect = null;
        synchronized (lock) {
            if (!running.get() || socket != current) {
                return;
            }
            boolean authenticated = authenticatedSockets.isAuthenticated(current);
            if (!SocketRetirementRules.mayRetire(
                    reason,
                    authenticated,
                    serverActivityWatchdog.isRetireDue(current, now),
                    socketInstalledAtElapsedMillis,
                    now,
                    AUTHENTICATION_TIMEOUT_MILLIS
            )) {
                return;
            }
            // Register the replacement before any close/cancel callback can run. If the
            // retirement request already prepared an immediate deadline, retain it.
            lifecycle.prepareBackoffReconnect(now, 0L, reason);
            reconnect = lifecycle.enterReconnectWait(reason);
            retiredEpoch = connectionEpoch;
            channelLanes.transportClosed(retiredEpoch);
            socket = null;
            challengedSocket = null;
            authenticatedSockets.clear(current);
            serverActivityWatchdog.clear(current);
            cancelFuture(heartbeatFuture);
            cancelFuture(taskRecoveryFuture);
            heartbeatFuture = null;
            taskRecoveryFuture = null;
            socketInstalledAtElapsedMillis = NO_DEADLINE;
            supervisorRetireReason = "";
            connectionEpoch++;
            retired = true;
        }
        if (!retired) {
            return;
        }
        publishLifecycle(reconnect, now);
        final long publishedRetiredEpoch = retiredEpoch;
        publishDiagnostic(() -> {
            CloudState.recordSocketRetirement(context, reason, publishedRetiredEpoch);
            AuditLog.append(context, "cloud_" + reason, false, null);
        });
        closeRetiredSocket(current, reason);
        wakeSupervisor();
    }

    private void closeRetiredSocket(WebSocket retired, String reason) {
        boolean closeAccepted;
        try {
            closeAccepted = retired.close(CLIENT_RECONNECT_CLOSE_CODE, reason);
        } catch (RuntimeException closeFailure) {
            closeAccepted = false;
        }
        if (!closeAccepted) {
            retired.cancel();
            return;
        }
        try {
            transportActor.schedule(
                    retired::cancel,
                    OLD_SOCKET_CANCEL_GRACE_MILLIS,
                    TimeUnit.MILLISECONDS
            );
        } catch (RejectedExecutionException schedulerStopped) {
            retired.cancel();
        }
    }

    private void requestSupervisorReconnectNow(String reason) {
        requireTransportActor();
        if (!running.get() || identityRevoked) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        CloudConnectionLifecycle.Snapshot reconnect = null;
        synchronized (lock) {
            if (socket == null) {
                reconnect = prepareImmediateAndWaitLocked(now, reason);
            }
        }
        if (reconnect != null) {
            publishLifecycle(reconnect, now);
        }
        try {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, now, reason));
        } finally {
            wakeSupervisor();
        }
    }

    private void requestSupervisorSocketRetirement(String reason) {
        requestSupervisorSocketRetirement(null, reason);
    }

    private boolean requestSupervisorSocketRetirement(
            WebSocket expectedSocket,
            String reason
    ) {
        requireTransportActor();
        if (!running.get() || identityRevoked) {
            return false;
        }
        long now = SystemClock.elapsedRealtime();
        CloudConnectionLifecycle.Snapshot prepared;
        synchronized (lock) {
            if (expectedSocket != null && socket != expectedSocket) {
                return false;
            }
            supervisorRetireReason = reason;
            lifecycle.prepareImmediateReconnect(now, reason);
            if (socket == null) {
                prepared = lifecycle.enterReconnectWait(reason);
            } else {
                prepared = lifecycle.markSuspect(reason);
            }
        }
        publishLifecycle(prepared, now);
        try {
            publishDiagnostic(() -> CloudState.recordSupervisorTick(
                    context, now, now, reason));
        } finally {
            wakeSupervisor();
        }
        return true;
    }

    private void wakeSupervisor() {
        try {
            postTransportEvent(this::runSupervisorSafely);
        } catch (RejectedExecutionException schedulerStopped) {
            publishDiagnostic(() -> CloudState.recordConnectionError(
                    context, "cloud_supervisor_unavailable"));
        }
    }

    private void sendHeartbeat(WebSocket current) {
        requireTransportActor();
        long runEpoch = authenticatedEpoch(current);
        if (runEpoch < 0L) {
            return;
        }
        SafeScheduledTask.run(
                () -> sendHeartbeatOnce(current),
                () -> recordTransportFailureForCurrent(
                        current,
                        runEpoch,
                        "heartbeat_scheduler",
                        "heartbeat_scheduler")
        );
    }

    private void sendHeartbeatOnce(WebSocket current) {
        requireTransportActor();
        final long sendEpoch;
        synchronized (lock) {
            if (socket != current || !running.get()) {
                return;
            }
            sendEpoch = connectionEpoch;
        }
        serviceTransportWatchdog.recordHeartbeatAttempt(SystemClock.elapsedRealtime());
        final String encoded;
        try {
            encoded = new JSONObject()
                    .put("type", "heartbeat")
                    .put("protocol_version", "1")
                    .toString();
        } catch (Exception serializationFailure) {
            throw new IllegalStateException(
                    "minimal_heartbeat_serialization_failed", serializationFailure);
        }
        final boolean accepted;
        try {
            accepted = current.send(encoded);
        } catch (RuntimeException websocketFailure) {
            if (recordTransportFailureForCurrent(
                    current, sendEpoch, "websocket_exception", "heartbeat_failed")) {
                requestSupervisorSocketRetirement(current, "heartbeat_failed");
            }
            return;
        }
        if (!accepted) {
            if (recordTransportFailureForCurrent(
                    current,
                    sendEpoch,
                    "heartbeat_send_rejected",
                    "heartbeat_failed")) {
                requestSupervisorSocketRetirement(current, "heartbeat_failed");
            }
            return;
        }
        synchronized (lock) {
            if (socket != current || connectionEpoch != sendEpoch) {
                return;
            }
        }
        publishDiagnostic(() -> CloudState.markHeartbeatSent(context, sendEpoch));
    }

    private void handleChallenge(WebSocket current, JSONObject message) throws Exception {
        requireTransportActor();
        requireExactFields(
                message,
                "type",
                "protocol_version",
                "challenge_id",
                "nonce",
                "issued_at",
                "algorithm",
                "signature_encoding"
        );
        if (!"1".equals(message.optString("protocol_version", ""))) {
            throw new IllegalArgumentException("unsupported_protocol_version");
        }
        if (!"ECDSA_P256_SHA256_DER".equals(message.optString("algorithm", "")) ||
                !"ASN1_DER".equals(message.optString("signature_encoding", ""))) {
            throw new IllegalArgumentException("unsupported_auth_signature_contract");
        }
        String challengeId = boundedText(message, "challenge_id", 80);
        String nonce = boundedText(message, "nonce", 128);
        long issuedAt = message.getLong("issued_at");
        if (issuedAt <= 0L) {
            throw new IllegalArgumentException("invalid_auth_challenge_time");
        }
        synchronized (lock) {
            if (socket != current || challengedSocket != null ||
                    authenticatedSockets.isAuthenticated(current)) {
                throw new IllegalStateException("unexpected_auth_challenge");
            }
            challengedSocket = current;
        }
        String deviceId = identityDeviceId;
        if (deviceId.isEmpty()) {
            throw new IllegalStateException("device_unbound");
        }
        TransportAuthProof.Prepared proof = TransportAuthProof.prepare(
                deviceId,
                challengeId,
                nonce,
                issuedAt,
                BuildConfig.VERSION_CODE,
                TransportAuthProof.TASK_STORE_PROTOCOL
        );
        final long signEpoch = connectionEpoch;
        final long signGeneration = ++signatureGeneration;
        final BoundedIoLane requestedLane = identityIoLane;
        boolean accepted = requestedLane.submit(() -> {
            String signature = "";
            Exception failure = null;
            try {
                signature = DeviceIdentity.sign(context, proof.message);
            } catch (Exception signFailure) {
                failure = signFailure;
            }
            String completedSignature = signature;
            Exception completedFailure = failure;
            postTransportEvent(() -> completeChallengeSignature(
                    current,
                    signEpoch,
                    signGeneration,
                    proof,
                    completedSignature,
                    completedFailure));
        });
        if (!accepted) {
            throw new IllegalStateException("identity_io_unavailable");
        }
        transportActor.schedule(
                () -> expireChallengeSignature(
                        current, signEpoch, signGeneration, requestedLane),
                IDENTITY_IO_DEADLINE_MILLIS,
                TimeUnit.MILLISECONDS);
    }

    private void expireChallengeSignature(
            WebSocket current,
            long signEpoch,
            long signGeneration,
            BoundedIoLane lane
    ) {
        requireTransportActor();
        if (signGeneration != signatureGeneration || signEpoch != connectionEpoch ||
                socket != current || challengedSocket != current) {
            return;
        }
        signatureGeneration++;
        rotateIdentityLane(lane);
        requestSupervisorSocketRetirement(current, "identity_signature_timeout");
    }

    private void rotateIdentityLane(BoundedIoLane timedOutLane) {
        requireTransportActor();
        if (identityIoLane != timedOutLane) {
            return;
        }
        timedOutLane.stopNow();
        abandonedIdentityLanes.add(timedOutLane);
        identityIoLane = null;
        ensureIdentityLaneCapacity();
    }

    private void ensureIdentityLaneCapacity() {
        requireTransportActor();
        abandonedIdentityLanes.removeIf(BoundedIoLane::isStopped);
        if (identityIoLane == null &&
                abandonedIdentityLanes.size() < MAX_CONCURRENT_ABANDONED_IO_LANES) {
            identityIoLane = new BoundedIoLane(
                    "oin-identity-io-r" + identityGeneration, 4);
        } else if (identityIoLane == null) {
            requestInfrastructureRecovery("identity_io_rotation_exhausted");
        }
    }

    private void scheduleIdentityRetry(String reason) {
        requireTransportActor();
        publishDiagnostic(() -> CloudState.recordConnectionError(context, reason));
        if (!running.get() || identityRevoked) {
            return;
        }
        transportActor.schedule(() -> {
            ensureIdentityLaneCapacity();
            if (!identityStateKnown && !identityPreflightInFlight) {
                beginReconnect(false);
            }
        }, 5L, TimeUnit.SECONDS);
    }

    private void completeChallengeSignature(
            WebSocket current,
            long signEpoch,
            long signGeneration,
            TransportAuthProof.Prepared proof,
            String signature,
            Exception failure
    ) {
        requireTransportActor();
        if (signGeneration != signatureGeneration || signEpoch != connectionEpoch ||
                socket != current || challengedSocket != current) {
            return;
        }
        if (failure != null) {
            handleProtocolFailure(current, failure);
            return;
        }
        try {
            if (!current.send(proof.requestBody(signature))) {
                throw new IllegalStateException("auth_send_rejected");
            }
        } catch (Exception sendFailure) {
            handleProtocolFailure(current, sendFailure);
        }
    }

    private void handleAuthOk(WebSocket current, JSONObject message) throws Exception {
        requireTransportActor();
        boolean hasRecoverySafeAfter = message.has("task_recovery_safe_after");
        boolean hasReconciliation = message.has("outbox_reconciliation");
        boolean hasServerSession = message.has("server_session_id");
        if (!hasServerSession) {
            throw new IllegalArgumentException("server_session_id_required");
        }
        if (hasRecoverySafeAfter && hasReconciliation) {
            requireExactFields(
                    message,
                    "type",
                    "protocol_version",
                    "device_id",
                    "heartbeat_interval_seconds",
                    "server_time",
                    "server_session_id",
                    "task_recovery_safe_after",
                    "outbox_reconciliation"
            );
        } else if (hasRecoverySafeAfter) {
            requireExactFields(
                    message,
                    "type",
                    "protocol_version",
                    "device_id",
                    "heartbeat_interval_seconds",
                    "server_time",
                    "server_session_id",
                    "task_recovery_safe_after"
            );
        } else if (hasReconciliation) {
            requireExactFields(
                    message,
                    "type",
                    "protocol_version",
                    "device_id",
                    "heartbeat_interval_seconds",
                    "server_time",
                    "server_session_id",
                    "outbox_reconciliation"
            );
        } else {
            requireExactFields(
                    message,
                    "type",
                    "protocol_version",
                    "device_id",
                    "heartbeat_interval_seconds",
                    "server_time",
                    "server_session_id"
            );
        }
        if (!"1".equals(message.optString("protocol_version", "")) ||
                !identityDeviceId.equals(boundedText(message, "device_id", 80))) {
            throw new IllegalArgumentException("auth_response_mismatch");
        }
        long serverTime = message.getLong("server_time");
        String serverSessionId = protocolUuid(message, "server_session_id");
        long heartbeatInterval = message.getLong("heartbeat_interval_seconds");
        long recoverySafeAfter = hasRecoverySafeAfter
                ? message.getLong("task_recovery_safe_after")
                : 0L;
        if (serverTime <= 0L || heartbeatInterval < 10L ||
                heartbeatInterval > MAX_SUPPORTED_HEARTBEAT_INTERVAL_SECONDS) {
            throw new IllegalArgumentException("invalid_auth_runtime_contract");
        }
        if (hasRecoverySafeAfter && recoverySafeAfter < serverTime) {
            throw new IllegalArgumentException("invalid_task_recovery_safe_after");
        }
        long authenticatedAt = SystemClock.elapsedRealtime();
        CloudConnectionLifecycle.Snapshot online;
        final long authenticatedEpoch;
        synchronized (lock) {
            if (socket != current || challengedSocket != current) {
                throw new IllegalStateException("stale_authenticated_socket");
            }
            authenticatedSockets.markAuthenticated(current);
            challengedSocket = null;
            socketInstalledAtElapsedMillis = NO_DEADLINE;
            serverClock.anchor(serverTime, authenticatedAt);
            serverActivityWatchdog.begin(current, authenticatedAt);
            channelLanes.authenticated(
                    connectionEpoch,
                    false,
                    TaskResultStore.RECOVERY_PENDING
            );
            currentHasRecoverySafeAfter = hasRecoverySafeAfter;
            currentRecoverySafeAfter = recoverySafeAfter;
            taskChannelRetryAttempt = 0;
            deviceStateRetryAttempt = 0;
            pendingDeviceStateAck = "";
            pendingDeviceStateAckEpoch = -1L;
            pendingDeviceStatePayload = "";
            deviceStateRefreshRequested = false;
            taskChannelWorkScheduled = false;
            taskChannelWorkEpoch = connectionEpoch;
            deviceStateWorkScheduled = false;
            deviceStateWorkEpoch = connectionEpoch;
            deviceStatePeriodicGeneration++;
            online = lifecycle.markOnlineFresh();
            authenticatedEpoch = connectionEpoch;
        }
        publishDiagnostic(() -> {
            CloudState.markServerActivity(context);
            CloudState.recordServerSession(context, serverSessionId);
            CloudState.recordTransportFailureStage(context, "none");
            CloudState.markDeviceStateStale(context);
            CloudState.recordSocketEpoch(context, authenticatedEpoch);
        });
        publishLifecycle(online, authenticatedAt);
        publishDiagnostic(() -> AuditLog.append(
                context, "cloud_authenticated", true, null));
        // Transport liveness is established before touching SQLite, pending replay,
        // reconciliation, PackageManager, permissions or any device snapshot work.
        startHeartbeat(current, heartbeatInterval);
        scheduleTaskChannelWork(current, 0L);
        scheduleDeviceStateRefresh(current, 0L);
        if (hasReconciliation) {
            scheduleOutboxReconciliation(
                    current,
                    message.getJSONObject("outbox_reconciliation")
            );
        }
    }

    private void scheduleTaskChannelWork(WebSocket current, long delaySeconds) {
        if (current == null || !running.get()) {
            return;
        }
        final long scheduledEpoch;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current)) {
                return;
            }
            scheduledEpoch = connectionEpoch;
            if (taskChannelWorkScheduled && taskChannelWorkEpoch == scheduledEpoch) {
                return;
            }
            taskChannelWorkScheduled = true;
            taskChannelWorkEpoch = scheduledEpoch;
        }
        try {
            taskChannelScheduler.schedule(() -> {
                boolean retry = false;
                try {
                    retry = runTaskChannelWork(current);
                } catch (Throwable failure) {
                    recordTaskChannelWorkerFailure(
                            current, scheduledEpoch, "outbox_load");
                    retry = true;
                } finally {
                    synchronized (lock) {
                        if (taskChannelWorkEpoch == scheduledEpoch) {
                            taskChannelWorkScheduled = false;
                        }
                    }
                }
                if (retry && running.get() &&
                        isAuthenticatedEpoch(current, scheduledEpoch)) {
                    scheduleTaskChannelWork(current, nextTaskChannelRetrySeconds());
                }
            }, Math.max(0L, delaySeconds), TimeUnit.SECONDS);
        } catch (RejectedExecutionException schedulerStopped) {
            synchronized (lock) {
                if (taskChannelWorkEpoch == scheduledEpoch) {
                    taskChannelWorkScheduled = false;
                }
            }
            publishDiagnostic(() -> {
                CloudState.recordOutboxFailureStage(context, "outbox_load");
                CloudState.recordConnectionError(
                        context, "task_channel_scheduler_unavailable");
            });
        }
    }

    private void recordTaskChannelWorkerFailure(
            WebSocket current,
            long workEpoch,
            String failureStage
    ) {
        synchronized (lock) {
            if (socket != current || connectionEpoch != workEpoch ||
                    !authenticatedSockets.isAuthenticated(current)) {
                return;
            }
            channelLanes.updateTaskChannel(
                    workEpoch, false, TaskResultStore.UNAVAILABLE);
        }
        publishDiagnostic(() -> {
            CloudState.recordOutboxFailureStage(context, failureStage);
            CloudState.recordConnectionError(context, "task_store_unavailable");
        });
        sendTaskChannelState(
                current, TaskResultStore.UNAVAILABLE, false, failureStage);
    }

    private boolean runTaskChannelWork(WebSocket current) {
        final long workEpoch;
        final boolean hasRecoverySafeAfter;
        final long recoverySafeAfter;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current)) {
                return false;
            }
            workEpoch = connectionEpoch;
            hasRecoverySafeAfter = currentHasRecoverySafeAfter;
            recoverySafeAfter = currentRecoverySafeAfter;
        }
        resultStore.prepareForCurrentScope();
        applyTaskRecoveryContract(current, hasRecoverySafeAfter, recoverySafeAfter);
        String state = resultStore.state();
        boolean ready = resultStore.isTaskChannelReady();
        if (!ready) {
            String wireState = TaskResultStore.RECOVERY_PENDING.equals(state)
                    ? TaskResultStore.RECOVERY_PENDING : TaskResultStore.UNAVAILABLE;
            String failureStage = TaskResultStore.UNAVAILABLE.equals(wireState)
                    ? "outbox_load" : "none";
            if (!updateTaskChannelLocalState(
                    current, workEpoch, false, wireState, failureStage)) {
                return false;
            }
            sendTaskChannelState(current, wireState, false, failureStage);
            return true;
        }
        if (!resendPendingResults(current)) {
            updateTaskChannelLocalState(
                    current,
                    workEpoch,
                    false,
                    TaskResultStore.UNAVAILABLE,
                    CloudState.outboxFailureStage(context));
            return true;
        }
        if (!updateTaskChannelLocalState(
                current, workEpoch, true, TaskResultStore.READY, "none")) {
            return false;
        }
        if (!sendTaskChannelState(
                current, TaskResultStore.READY, true, "none")) {
            return true;
        }
        synchronized (lock) {
            if (socket == current && connectionEpoch == workEpoch) {
                taskChannelRetryAttempt = 0;
            }
        }
        return false;
    }

    private boolean updateTaskChannelLocalState(
            WebSocket current,
            long workEpoch,
            boolean ready,
            String state,
            String failureStage
    ) {
        boolean recordFailure = false;
        synchronized (lock) {
            if (socket != current || connectionEpoch != workEpoch ||
                    !authenticatedSockets.isAuthenticated(current)) {
                return false;
            }
            if (!channelLanes.updateTaskChannel(workEpoch, ready, state)) {
                return false;
            }
            if (!"none".equals(failureStage)) {
                recordFailure = true;
            }
        }
        if (recordFailure) {
            publishDiagnostic(() -> CloudState.recordOutboxFailureStage(
                    context, failureStage));
        }
        return true;
    }

    private long nextTaskChannelRetrySeconds() {
        synchronized (lock) {
            taskChannelRetryAttempt = Math.min(10, taskChannelRetryAttempt + 1);
            long delay = 1L << Math.min(6, taskChannelRetryAttempt - 1);
            return Math.min(TASK_CHANNEL_RETRY_MAX_SECONDS, delay);
        }
    }

    private boolean sendTaskChannelState(
            WebSocket current,
            String state,
            boolean ready,
            String failureStage
    ) {
        long sendEpoch = authenticatedEpoch(current);
        if (sendEpoch < 0L) {
            return false;
        }
        try {
            JSONObject update = new JSONObject()
                    .put("type", "task_channel_state")
                    .put("protocol_version", "1")
                    .put("task_store_protocol", 2)
                    .put("state", state)
                    .put("ready", ready)
                    .put("failure_stage", failureStage);
            if (current.send(update.toString())) {
                return true;
            }
        } catch (RuntimeException websocketFailure) {
            recordOutboxSendFailureForCurrent(current, sendEpoch, true);
        } catch (Exception serializationFailure) {
            if (isAuthenticatedEpoch(current, sendEpoch)) {
                CloudState.recordOutboxFailureStage(context, "outbox_replay");
            }
            return false;
        }
        if (recordOutboxSendFailureForCurrent(current, sendEpoch, false)) {
            requestOutboxReplayReconnect(
                    current, "pending_task_result_delivery_reconnect");
        }
        return false;
    }

    private void scheduleDeviceStateRefresh(WebSocket current, long delaySeconds) {
        if (current == null || !running.get()) {
            return;
        }
        final long scheduledEpoch;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current)) {
                return;
            }
            scheduledEpoch = connectionEpoch;
            if (pendingDeviceStateAckEpoch == scheduledEpoch &&
                    !pendingDeviceStateAck.isEmpty()) {
                if (delaySeconds == 0L) {
                    deviceStateRefreshRequested = true;
                }
                return;
            }
            if (deviceStateWorkScheduled && deviceStateWorkEpoch == scheduledEpoch) {
                if (delaySeconds == 0L) {
                    deviceStateRefreshRequested = true;
                }
                return;
            }
            deviceStateWorkScheduled = true;
            deviceStateWorkEpoch = scheduledEpoch;
        }
        try {
            deviceStateScheduler.schedule(() -> {
                boolean retry;
                boolean refreshRequested = false;
                try {
                    retry = !sendDeviceStateRefresh(current);
                } catch (Throwable failure) {
                    if (isAuthenticatedEpoch(current, scheduledEpoch)) {
                        recordDeviceStateFailure(current, "device_snapshot_collect");
                    }
                    retry = true;
                } finally {
                    synchronized (lock) {
                        if (deviceStateWorkEpoch == scheduledEpoch) {
                            deviceStateWorkScheduled = false;
                            if (pendingDeviceStateAck.isEmpty() &&
                                    deviceStateRefreshRequested) {
                                deviceStateRefreshRequested = false;
                                refreshRequested = true;
                            }
                        }
                    }
                }
                if (refreshRequested && running.get() &&
                        isAuthenticatedEpoch(current, scheduledEpoch)) {
                    scheduleDeviceStateRefresh(current, 0L);
                } else if (retry && running.get() &&
                        isAuthenticatedEpoch(current, scheduledEpoch)) {
                    scheduleDeviceStateRefresh(current, nextDeviceStateRetrySeconds());
                }
            }, Math.max(0L, delaySeconds), TimeUnit.SECONDS);
        } catch (RejectedExecutionException schedulerStopped) {
            synchronized (lock) {
                if (deviceStateWorkEpoch == scheduledEpoch) {
                    deviceStateWorkScheduled = false;
                }
            }
            publishDiagnostic(() -> CloudState.recordDeviceStateFailure(
                    context, "device_snapshot_collect"));
        }
    }

    private boolean sendDeviceStateRefresh(WebSocket current) {
        final long workEpoch;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current)) {
                return true;
            }
            workEpoch = connectionEpoch;
        }
        final JSONObject raw;
        try {
            raw = DeviceSnapshot.create(context);
        } catch (Exception collectionFailure) {
            recordDeviceStateFailure(current, "device_snapshot_collect");
            return false;
        }
        final JSONObject normalized;
        try {
            normalized = new JSONObject(
                    DevicePayloadNormalizer.normalize(CanonicalJson.toMap(raw))
            );
        } catch (Exception normalizationFailure) {
            recordDeviceStateFailure(current, "device_snapshot_normalize");
            return false;
        }
        final String encoded;
        try {
            encoded = new JSONObject()
                    .put("type", "device_state_update")
                    .put("protocol_version", "1")
                    .put("state", "fresh")
                    .put("failure_stage", "none")
                    .put("payload", normalized)
                    .toString();
        } catch (Exception serializationFailure) {
            recordDeviceStateFailure(current, "device_snapshot_serialize");
            return false;
        }
        if (!sendDeviceStateMessage(
                current, workEpoch, "fresh", encoded, normalized.toString())) {
            if (isAuthenticatedEpoch(current, workEpoch)) {
                CloudState.recordDeviceStateFailure(context, "device_snapshot_send");
                channelLanes.updateDeviceState(workEpoch, ChannelLaneState.DEVICE_STALE);
            }
            return false;
        }
        scheduleDeviceStateAckCheck(current, workEpoch);
        synchronized (lock) {
            if (socket == current && connectionEpoch == workEpoch) {
                deviceStateRetryAttempt = 0;
            }
        }
        return true;
    }

    private void scheduleDeviceStateAckCheck(WebSocket current, long workEpoch) {
        try {
            deviceStateScheduler.schedule(() -> {
                synchronized (lock) {
                    if (socket != current || connectionEpoch != workEpoch ||
                            !authenticatedSockets.isAuthenticated(current) ||
                            pendingDeviceStateAckEpoch != workEpoch ||
                            pendingDeviceStateAck.isEmpty()) {
                        return;
                    }
                    pendingDeviceStateAck = "";
                    pendingDeviceStateAckEpoch = -1L;
                    pendingDeviceStatePayload = "";
                    deviceStateRefreshRequested = false;
                }
                recordDeviceStateFailure(current, "device_snapshot_send");
                scheduleDeviceStateRefresh(current, 0L);
            }, DEVICE_STATE_ACK_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } catch (RejectedExecutionException schedulerStopped) {
            recordDeviceStateFailure(current, "device_snapshot_send");
        }
    }

    private void schedulePeriodicDeviceStateRefresh(
            WebSocket current,
            long acknowledgedEpoch
    ) {
        final long generation;
        synchronized (lock) {
            if (socket != current || connectionEpoch != acknowledgedEpoch ||
                    !authenticatedSockets.isAuthenticated(current)) {
                return;
            }
            generation = ++deviceStatePeriodicGeneration;
        }
        try {
            deviceStateScheduler.schedule(() -> {
                synchronized (lock) {
                    if (socket != current || connectionEpoch != acknowledgedEpoch ||
                            generation != deviceStatePeriodicGeneration ||
                            !authenticatedSockets.isAuthenticated(current)) {
                        return;
                    }
                }
                scheduleDeviceStateRefresh(current, 0L);
            }, DEVICE_STATE_REFRESH_SECONDS, TimeUnit.SECONDS);
        } catch (RejectedExecutionException schedulerStopped) {
            CloudState.recordDeviceStateFailure(context, "device_snapshot_collect");
        }
    }

    private void recordDeviceStateFailure(WebSocket current, String failureStage) {
        final long workEpoch;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current)) {
                return;
            }
            workEpoch = connectionEpoch;
            channelLanes.updateDeviceState(workEpoch, ChannelLaneState.DEVICE_STALE);
        }
        publishDiagnostic(() -> CloudState.recordDeviceStateFailure(context, failureStage));
        try {
            String encoded = new JSONObject()
                    .put("type", "device_state_update")
                    .put("protocol_version", "1")
                    .put("state", "stale")
                    .put("failure_stage", failureStage)
                    .toString();
            if (!sendDeviceStateMessage(current, workEpoch, "stale", encoded, "")) {
                if (isAuthenticatedEpoch(current, workEpoch)) {
                    CloudState.recordDeviceStateFailure(context, "device_snapshot_send");
                }
            }
        } catch (Exception serializationFailure) {
            if (isAuthenticatedEpoch(current, workEpoch)) {
                CloudState.recordDeviceStateFailure(context, "device_snapshot_serialize");
            }
        }
    }

    private boolean sendDeviceStateMessage(
            WebSocket current,
            long workEpoch,
            String state,
            String encoded,
            String confirmedPayload
    ) {
        final boolean accepted;
        try {
            accepted = current.send(encoded);
        } catch (RuntimeException websocketFailure) {
            recordTransportFailureForCurrent(
                    current, workEpoch, "websocket_exception", null);
            return false;
        }
        if (!accepted) {
            return false;
        }
        synchronized (lock) {
            if (socket != current || connectionEpoch != workEpoch ||
                    !authenticatedSockets.isAuthenticated(current)) {
                return false;
            }
            if (ChannelLaneState.DEVICE_FRESH.equals(state)) {
                pendingDeviceStateAck = state;
                pendingDeviceStateAckEpoch = workEpoch;
                pendingDeviceStatePayload = confirmedPayload;
            }
        }
        return true;
    }

    private long nextDeviceStateRetrySeconds() {
        synchronized (lock) {
            deviceStateRetryAttempt = Math.min(10, deviceStateRetryAttempt + 1);
            long delay = 1L << Math.min(6, deviceStateRetryAttempt - 1);
            return Math.min(DEVICE_STATE_RETRY_MAX_SECONDS, delay);
        }
    }

    private void handleTaskChannelStateAck(WebSocket current, JSONObject message)
            throws Exception {
        requireExactFields(
                message,
                "type",
                "protocol_version",
                "task_store_protocol",
                "state",
                "ready",
                "server_time"
        );
        if (!"1".equals(message.optString("protocol_version", "")) ||
                message.getInt("task_store_protocol") != 2) {
            throw new IllegalArgumentException("task_channel_state_ack_mismatch");
        }
        String state = boundedText(message, "state", 32);
        boolean ready = message.getBoolean("ready");
        if (ready != TaskResultStore.READY.equals(state)) {
            throw new IllegalArgumentException("task_channel_state_ack_mismatch");
        }
        boundedLong(message, "server_time", 1L, Long.MAX_VALUE);
        boolean clearFailure = false;
        long acknowledgedEpoch = -1L;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current)) {
                return;
            }
            if (ready) {
                clearFailure = true;
                acknowledgedEpoch = connectionEpoch;
            }
        }
        if (clearFailure) {
            publishDiagnostic(() -> CloudState.recordOutboxFailureStage(context, "none"));
            final long readyEpoch = acknowledgedEpoch;
            postTransportEvent(() -> {
                requireTransportActor();
                if (isAuthenticatedEpoch(current, readyEpoch) &&
                        channelLanes.isTaskChannelReady()) {
                    completeRecoveryEpisode(SystemClock.elapsedRealtime());
                }
            });
        }
    }

    private void handleDeviceStateAck(WebSocket current, JSONObject message) throws Exception {
        requireExactFields(
                message, "type", "protocol_version", "state", "server_time");
        if (!"1".equals(message.optString("protocol_version", ""))) {
            throw new IllegalArgumentException("unsupported_protocol_version");
        }
        String state = boundedText(message, "state", 16);
        if (!("fresh".equals(state) || "stale".equals(state))) {
            throw new IllegalArgumentException("invalid_device_state_ack");
        }
        boundedLong(message, "server_time", 1L, Long.MAX_VALUE);
        final long acknowledgedEpoch;
        final String confirmedPayload;
        final boolean refreshRequested;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current) ||
                    pendingDeviceStateAckEpoch != connectionEpoch ||
                    !state.equals(pendingDeviceStateAck)) {
                return;
            }
            acknowledgedEpoch = connectionEpoch;
            confirmedPayload = pendingDeviceStatePayload;
            pendingDeviceStateAck = "";
            pendingDeviceStateAckEpoch = -1L;
            pendingDeviceStatePayload = "";
            refreshRequested = deviceStateRefreshRequested;
            deviceStateRefreshRequested = false;
        }
        scheduleDeviceStateAckCompletion(
                current,
                acknowledgedEpoch,
                state,
                confirmedPayload,
                refreshRequested
        );
    }

    private void scheduleDeviceStateAckCompletion(
            WebSocket current,
            long acknowledgedEpoch,
            String state,
            String confirmedPayload,
            boolean refreshRequested
    ) {
        try {
            deviceStateScheduler.execute(() -> completeDeviceStateAck(
                    current,
                    acknowledgedEpoch,
                    state,
                    confirmedPayload,
                    refreshRequested
            ));
        } catch (RejectedExecutionException schedulerStopped) {
            recordDeviceStateFailure(current, "device_state_cache_store");
        }
    }

    private void completeDeviceStateAck(
            WebSocket current,
            long acknowledgedEpoch,
            String state,
            String confirmedPayload,
            boolean refreshRequested
    ) {
        if (!isAuthenticatedEpoch(current, acknowledgedEpoch)) {
            return;
        }
        boolean cacheStored = true;
        if (ChannelLaneState.DEVICE_FRESH.equals(state) && !confirmedPayload.isEmpty()) {
            try {
                cacheStored = DeviceStateCache.store(
                        context, new JSONObject(confirmedPayload));
            } catch (Exception cacheFailure) {
                cacheStored = false;
            }
        }
        if (!cacheStored) {
            recordDeviceStateFailure(current, "device_state_cache_store");
            scheduleDeviceStateRefresh(current, nextDeviceStateRetrySeconds());
            return;
        }
        synchronized (lock) {
            if (socket != current || connectionEpoch != acknowledgedEpoch ||
                    !authenticatedSockets.isAuthenticated(current)) {
                return;
            }
            channelLanes.updateDeviceState(acknowledgedEpoch, state);
        }
        publishDiagnostic(() -> CloudState.recordDeviceStateConfirmed(context, state));
        if (refreshRequested) {
            scheduleDeviceStateRefresh(current, 0L);
            return;
        }
        if (ChannelLaneState.DEVICE_FRESH.equals(state)) {
            schedulePeriodicDeviceStateRefresh(current, acknowledgedEpoch);
        }
    }

    private boolean isAuthenticatedEpoch(WebSocket current, long expectedEpoch) {
        synchronized (lock) {
            return socket == current && connectionEpoch == expectedEpoch &&
                    authenticatedSockets.isAuthenticated(current);
        }
    }

    private long authenticatedEpoch(WebSocket current) {
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current)) {
                return -1L;
            }
            return connectionEpoch;
        }
    }

    private boolean recordTransportFailureForCurrent(
            WebSocket current,
            long expectedEpoch,
            String failureStage,
            String errorCode
    ) {
        synchronized (lock) {
            if (socket != current || connectionEpoch != expectedEpoch ||
                    !authenticatedSockets.isAuthenticated(current)) {
                return false;
            }
        }
        publishDiagnostic(() -> {
            CloudState.recordTransportFailureStage(context, failureStage);
            if (errorCode != null) {
                CloudState.recordConnectionError(context, errorCode);
            }
        });
        return true;
    }

    private boolean recordOutboxSendFailureForCurrent(
            WebSocket current,
            long expectedEpoch,
            boolean websocketException
    ) {
        synchronized (lock) {
            if (socket != current || connectionEpoch != expectedEpoch ||
                    !authenticatedSockets.isAuthenticated(current)) {
                return false;
            }
        }
        publishDiagnostic(() -> {
            if (websocketException) {
                CloudState.recordTransportFailureStage(context, "websocket_exception");
            }
            CloudState.recordOutboxFailureStage(context, "outbox_send");
        });
        return true;
    }

    private void scheduleOutboxReconciliation(
            WebSocket current,
            JSONObject directive
    ) throws Exception {
        requireExactFields(
                directive,
                "authorization_id",
                "challenge",
                "old_scope",
                "new_scope",
                "expires_at"
        );
        String authorizationId = protocolUuid(directive, "authorization_id");
        String challenge = boundedText(directive, "challenge", 128);
        String oldScope = protocolUuid(directive, "old_scope");
        String newScope = protocolUuid(directive, "new_scope");
        long expiresAt = boundedLong(directive, "expires_at", 1L, Long.MAX_VALUE);
        if (!identityDeviceId.equals(newScope) || oldScope.equals(newScope)) {
            throw new IllegalArgumentException("reconciliation_scope_mismatch");
        }
        if (!reconciliationSendScheduled.compareAndSet(false, true)) {
            return;
        }
        try {
            taskChannelScheduler.execute(() -> {
                try {
                    sendOutboxReconciliation(
                            current,
                            authorizationId,
                            challenge,
                            oldScope,
                            newScope,
                            expiresAt
                    );
                } finally {
                    reconciliationSendScheduled.set(false);
                }
            });
        } catch (RejectedExecutionException schedulerStopped) {
            reconciliationSendScheduled.set(false);
            publishDiagnostic(() -> CloudState.recordConnectionError(
                    context, "outbox_reconciliation_scheduler_unavailable"));
        }
    }

    private void sendOutboxReconciliation(
            WebSocket current,
            String authorizationId,
            String challenge,
            String oldScope,
            String newScope,
            long expiresAt
    ) {
        try {
            boolean locallyComplete = resultStore.isReconciliationLocallyComplete(
                    authorizationId, oldScope, newScope
            );
            List<TaskResultStore.ReconciliationRecord> records = locallyComplete
                    ? new java.util.ArrayList<>()
                    : resultStore.foreignNonterminalRecords(oldScope, newScope);
            ReconciliationSession session = new ReconciliationSession(
                    authorizationId,
                    challenge,
                    oldScope,
                    newScope,
                    expiresAt,
                    records
            );
            synchronized (lock) {
                if (socket != current || !authenticatedSockets.isAuthenticated(current)) {
                    return;
                }
                activeReconciliation = session;
            }
            if (locallyComplete) {
                sendReconciliationComplete(current, session);
                return;
            }
            org.json.JSONArray manifest = new org.json.JSONArray();
            for (TaskResultStore.ReconciliationRecord record : records) {
                manifest.put(new JSONObject()
                        .put("task_id", record.taskId)
                        .put("local_state", record.localState)
                        .put("result_sha256", record.resultSha256)
                        .put("result_bytes", record.resultBytes));
            }
            JSONObject begin = new JSONObject()
                    .put("type", "outbox_reconciliation_begin")
                    .put("protocol_version", "1")
                    .put("authorization_id", authorizationId)
                    .put("challenge", challenge)
                    .put("old_scope", oldScope)
                    .put("new_scope", newScope)
                    .put("items", manifest);
            if (!sendOutboxWireMessage(
                    current,
                    begin.toString(),
                    "outbox_reconciliation_delivery_reconnect")) {
                return;
            }
            for (TaskResultStore.ReconciliationRecord record : records) {
                JSONObject item = new JSONObject()
                        .put("type", "outbox_reconciliation_item")
                        .put("protocol_version", "1")
                        .put("authorization_id", authorizationId)
                        .put("challenge", challenge)
                        .put("old_scope", oldScope)
                        .put("new_scope", newScope)
                        .put("task_id", record.taskId)
                        .put("local_state", record.localState);
                if ("PENDING".equals(record.localState)) {
                    JSONObject stored = new JSONObject(record.resultMessage);
                    String status = boundedText(stored, "status", 20);
                    item.put("status", status);
                    if ("succeeded".equals(status)) {
                        item.put("result", stored.get("result"));
                    } else {
                        item.put("error", stored.getJSONObject("error"));
                    }
                }
                if (!sendOutboxWireMessage(
                        current,
                        item.toString(),
                        "outbox_reconciliation_delivery_reconnect")) {
                    return;
                }
            }
        } catch (RuntimeException storageFailure) {
            handleResultStoreFailure(current, storageFailure);
        } catch (Exception protocolFailure) {
            CloudState.recordOutboxFailureStage(context, "outbox_replay");
            CloudState.recordConnectionError(context, "outbox_reconciliation_protocol_error");
            scheduleTaskChannelWork(current, nextTaskChannelRetrySeconds());
        }
    }

    private void handleReconciliationBeginAck(WebSocket current, JSONObject message)
            throws Exception {
        requireExactFields(
                message,
                "type",
                "protocol_version",
                "authorization_id",
                "old_scope",
                "new_scope",
                "item_count",
                "reconciliation_complete"
        );
        if (!"1".equals(message.optString("protocol_version", ""))) {
            throw new IllegalArgumentException("unsupported_protocol_version");
        }
        String authorizationId = protocolUuid(message, "authorization_id");
        String oldScope = protocolUuid(message, "old_scope");
        String newScope = protocolUuid(message, "new_scope");
        int itemCount = (int) boundedLong(
                message, "item_count", 0L, TaskResultStore.MAX_IN_FLIGHT
        );
        boolean complete = message.getBoolean("reconciliation_complete");
        ReconciliationSession session = currentReconciliation(
                current, authorizationId, oldScope, newScope
        );
        if (session.records.size() != itemCount) {
            throw new IllegalArgumentException("reconciliation_manifest_count_mismatch");
        }
        if (itemCount == 0 && complete) {
            resultStore.markEmptyReconciliationComplete(
                    authorizationId, oldScope, newScope
            );
            sendReconciliationComplete(current, session);
        }
    }

    private void handleReconciliationItemAck(WebSocket current, JSONObject message)
            throws Exception {
        requireExactFields(
                message,
                "type",
                "protocol_version",
                "authorization_id",
                "old_scope",
                "new_scope",
                "task_id",
                "local_state",
                "result_sha256",
                "result_bytes",
                "disposition",
                "reconciliation_complete"
        );
        if (!"1".equals(message.optString("protocol_version", ""))) {
            throw new IllegalArgumentException("unsupported_protocol_version");
        }
        String authorizationId = protocolUuid(message, "authorization_id");
        String oldScope = protocolUuid(message, "old_scope");
        String newScope = protocolUuid(message, "new_scope");
        String taskId = protocolUuid(message, "task_id");
        String localState = boundedText(message, "local_state", 12);
        String resultSha256 = message.optString("result_sha256", "");
        int resultBytes = (int) boundedLong(
                message, "result_bytes", 0L, TaskResultStore.MAX_RESULT_BYTES
        );
        String disposition = optionalBoundedCode(
                message, "disposition", "reconciliation_ack_invalid"
        );
        boolean complete = message.getBoolean("reconciliation_complete");
        ReconciliationSession session = currentReconciliation(
                current, authorizationId, oldScope, newScope
        );
        TaskResultStore.ReconciliationRecord expected = session.record(taskId);
        if (expected == null || !expected.localState.equals(localState) ||
                !expected.resultSha256.equals(resultSha256) ||
                expected.resultBytes != resultBytes) {
            throw new IllegalArgumentException("reconciliation_item_ack_mismatch");
        }
        resultStore.archiveReconciled(
                authorizationId,
                oldScope,
                newScope,
                taskId,
                localState,
                resultSha256,
                resultBytes,
                disposition
        );
        if (complete && resultStore.isReconciliationLocallyComplete(
                authorizationId, oldScope, newScope
        )) {
            sendReconciliationComplete(current, session);
        }
    }

    private void sendReconciliationComplete(
            WebSocket current,
            ReconciliationSession session
    ) throws Exception {
        JSONObject complete = new JSONObject()
                .put("type", "outbox_reconciliation_complete")
                .put("protocol_version", "1")
                .put("authorization_id", session.authorizationId)
                .put("challenge", session.challenge)
                .put("old_scope", session.oldScope)
                .put("new_scope", session.newScope);
        sendOutboxWireMessage(
                current,
                complete.toString(),
                "outbox_reconciliation_delivery_reconnect");
    }

    private void handleReconciliationCompleteAck(WebSocket current, JSONObject message)
            throws Exception {
        requireExactFields(
                message,
                "type",
                "protocol_version",
                "authorization_id",
                "old_scope",
                "new_scope",
                "device_acknowledged_at"
        );
        if (!"1".equals(message.optString("protocol_version", ""))) {
            throw new IllegalArgumentException("unsupported_protocol_version");
        }
        String authorizationId = protocolUuid(message, "authorization_id");
        String oldScope = protocolUuid(message, "old_scope");
        String newScope = protocolUuid(message, "new_scope");
        boundedLong(message, "device_acknowledged_at", 1L, Long.MAX_VALUE);
        currentReconciliation(current, authorizationId, oldScope, newScope);
        resultStore.acknowledgeReconciliationComplete(authorizationId);
        synchronized (lock) {
            if (activeReconciliation != null &&
                    activeReconciliation.matches(authorizationId, oldScope, newScope)) {
                activeReconciliation = null;
            }
        }
        scheduleTaskChannelWork(current, 0L);
    }

    private ReconciliationSession currentReconciliation(
            WebSocket current,
            String authorizationId,
            String oldScope,
            String newScope
    ) {
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current) ||
                    activeReconciliation == null ||
                    !activeReconciliation.matches(authorizationId, oldScope, newScope)) {
                throw new IllegalArgumentException("stale_reconciliation_ack");
            }
            return activeReconciliation;
        }
    }

    private void applyTaskRecoveryContract(
            WebSocket current,
            boolean hasRecoverySafeAfter,
            long recoverySafeAfter
    ) {
        if (!resultStore.isRecoveryPending()) {
            return;
        }
        if (!hasRecoverySafeAfter) {
            AuditLog.append(context, "task_store_recovery_waiting_for_v2_server", false, null);
            return;
        }
        resultStore.recordRecoverySafeAfter(recoverySafeAfter);
        if (!resultStore.isRecoveryPending()) {
            return;
        }
        if (resultStore.completeRecovery(trustedNowSeconds())) {
            AuditLog.append(context, "task_store_recovery_channel_ready", true, null);
            return;
        }
        if (isAdministrativeTaskRecoveryBlock()) {
            AuditLog.append(context, "task_store_recovery_scope_mismatch", false, null);
            return;
        }
        scheduleTaskRecovery(current);
    }

    private void scheduleTaskRecovery(WebSocket current) {
        long safeAfter = resultStore.recoverySafeAfter();
        if (safeAfter <= 0L || !resultStore.isRecoveryPending()) {
            return;
        }
        long delaySeconds = Math.max(1L, safeAfter - trustedNowSeconds());
        boolean schedulerUnavailable = false;
        synchronized (lock) {
            if (socket != current || !authenticatedSockets.isAuthenticated(current) ||
                    !running.get()) {
                return;
            }
            cancelFuture(taskRecoveryFuture);
            try {
                taskRecoveryFuture = taskChannelScheduler.schedule(
                        () -> completeScheduledTaskRecovery(current),
                        delaySeconds,
                        TimeUnit.SECONDS
                );
            } catch (RejectedExecutionException schedulerStopped) {
                taskRecoveryFuture = null;
                schedulerUnavailable = true;
            }
        }
        if (schedulerUnavailable) {
            publishDiagnostic(() -> CloudState.recordConnectionError(
                    context, "task_store_recovery_scheduler_unavailable"));
        }
    }

    private void completeScheduledTaskRecovery(WebSocket current) {
        synchronized (lock) {
            taskRecoveryFuture = null;
            if (socket != current || !authenticatedSockets.isAuthenticated(current) ||
                    !running.get()) {
                return;
            }
        }
        if (resultStore.completeRecovery(trustedNowSeconds())) {
            AuditLog.append(context, "task_store_recovery_channel_ready", true, null);
            scheduleTaskChannelWork(current, 0L);
        } else if (resultStore.isRecoveryPending() && !isAdministrativeTaskRecoveryBlock()) {
            scheduleTaskRecovery(current);
        }
    }

    private boolean isAdministrativeTaskRecoveryBlock() {
        String code = resultStore.diagnosticCode();
        return "legacy_scope_mismatch".equals(code) ||
                "foreign_scope_nonterminal".equals(code) ||
                "legacy_current_scope_unbound".equals(code);
    }

    private boolean resendPendingResults(WebSocket current) {
        if (!resultStore.isTaskChannelReady()) {
            return true;
        }
        final java.util.List<String> pending;
        try {
            pending = resultStore.pendingMessages();
        } catch (RuntimeException storageFailure) {
            CloudState.recordOutboxFailureStage(context, "outbox_load");
            handleResultStoreFailure(current, storageFailure);
            return false;
        }
        for (String message : pending) {
            if (!sendPersistedResult(message, "pending_task_result_delivery_reconnect")) {
                CloudState.recordOutboxFailureStage(context, "outbox_send");
                return false;
            }
        }
        return true;
    }

    private void handleTask(
            WebSocket current,
            JSONObject message,
            TaskAdmissionPermit permit
    ) throws Exception {
        synchronized (taskAdmissionLock) {
            if (!TaskStoreRules.canAdmitTask(
                    running.get(),
                    isAuthenticated(current),
                    !taskExecutor.isShutdown()
            )) {
                AuditLog.append(context, "task_rejected_channel_quiescing", false, null);
                return;
            }
            handleAdmittedTask(current, message, permit);
        }
    }

    private void dispatchTaskAdmission(WebSocket current, JSONObject message) {
        requireTransportActor();
        long generation = taskAdmissionWatchdog.begin();
        if (generation < 0L || taskAdmissionIoLane == null) {
            publishDiagnostic(() -> CloudState.recordConnectionError(
                    context, "task_admission_degraded"));
            return;
        }
        BoundedIoLane lane = taskAdmissionIoLane;
        TaskAdmissionPermit permit = new TaskAdmissionPermit(TASK_ADMISSION_DEADLINE_MILLIS);
        boolean accepted = lane.submit(() -> {
            Exception failure = null;
            try {
                handleTask(current, message, permit);
            } catch (Exception taskFailure) {
                failure = taskFailure;
            }
            Exception completedFailure = failure;
            postTransportEvent(() -> {
                boolean admissionTimedOut = taskAdmissionWatchdog.timedOut();
                if (!taskAdmissionWatchdog.complete(generation)) {
                    return;
                }
                if (completedFailure != null && !admissionTimedOut) {
                    handleProtocolFailure(current, completedFailure);
                }
            });
        });
        if (!accepted) {
            taskAdmissionWatchdog.complete(generation);
            return;
        }
        transportActor.schedule(
                () -> expireTaskAdmission(generation, permit),
                TASK_ADMISSION_DEADLINE_MILLIS,
                TimeUnit.MILLISECONDS);
    }

    private void expireTaskAdmission(long generation, TaskAdmissionPermit permit) {
        requireTransportActor();
        if (!taskAdmissionWatchdog.timeout(generation)) {
            return;
        }
        permit.expire();
        publishDiagnostic(() -> CloudState.recordConnectionError(
                context, "task_admission_timeout_degraded"));
        // Deliberately no ACK and no transport retirement: the server lease may re-offer the
        // attempt, while transport heartbeats and unrelated channel work remain healthy.
    }

    private void handleAdmittedTask(
            WebSocket current,
            JSONObject message,
            TaskAdmissionPermit permit
    ) throws Exception {
        if (!channelLanes.isTaskChannelReady() || !resultStore.isTaskChannelReady()) {
            AuditLog.append(context, "task_rejected_channel_not_ready", false, null);
            CloudState.recordConnectionError(context, "task_channel_not_ready");
            scheduleTaskChannelWork(current, 0L);
            return;
        }
        requireExactFields(
                message,
                "type",
                "protocol_version",
                "task_id",
                "task_type",
                "issued_at",
                "expires_at",
                "parameters",
                "delivery_attempt_id"
        );
        String taskId = boundedText(message, "task_id", 80);
        String deliveryAttemptId = DeliveryAttemptContract.canonicalAttemptId(
                boundedText(message, "delivery_attempt_id", 80));
        try {
            if (!UUID.fromString(taskId).toString().equals(taskId)) {
                throw new IllegalArgumentException("non_canonical_task_id");
            }
        } catch (IllegalArgumentException invalidId) {
            sendRejected(current, taskId, "invalid_task_id");
            return;
        }
        String taskType = boundedText(message, "task_type", 40);
        long issuedAt = message.getLong("issued_at");
        long expiresAt = message.getLong("expires_at");
        long now = trustedNowSeconds();
        JSONObject parameters = message.optJSONObject("parameters");
        if (!"1".equals(message.optString("protocol_version", ""))) {
            persistRejectedTask(current, taskId, deliveryAttemptId, permit,
                    "unsupported_protocol_version");
            return;
        }
        if (!ALLOWED_TASKS.contains(taskType)) {
            persistRejectedTask(current, taskId, deliveryAttemptId, permit,
                    "unknown_task_type");
            return;
        }
        String parameterError = validateTaskParameters(taskType, parameters);
        if (parameterError != null) {
            persistRejectedTask(current, taskId, deliveryAttemptId, permit, parameterError);
            return;
        }
        if (issuedAt > now + 30L || expiresAt <= now || expiresAt <= issuedAt ||
                expiresAt - issuedAt > 300L) {
            persistRejectedTask(current, taskId, deliveryAttemptId, permit,
                    "task_expired_or_invalid_time");
            return;
        }
        final TaskResultLedger.Claim claim;
        try {
            claim = resultStore.claim(taskId, permit);
        } catch (RuntimeException storageFailure) {
            handleResultStoreFailure(current, storageFailure);
            return;
        }
        if (claim.kind == TaskResultLedger.ClaimKind.DEADLINE_EXPIRED) {
            // The same transaction inserted a durable PENDING failure. Do not ACK the expired
            // delivery; the next attempt will ACK and replay this exact result.
            return;
        }
        switch (claim.kind) {
            case PENDING:
                sendTaskAck(current, taskId, deliveryAttemptId);
                sendPersistedResult(
                        claim.pendingMessage,
                        "task_result_resend_reconnect",
                        claim.deviceScope
                );
                return;
            case RUNNING:
            case COMPLETED:
                sendTaskAck(current, taskId, deliveryAttemptId);
                return;
            case CAPACITY:
                CloudState.recordConnectionError(context, "task_result_outbox_capacity");
                CloudState.recordOutboxFailureStage(context, "outbox_load");
                channelLanes.updateTaskChannel(
                        connectionEpoch, false, TaskResultStore.UNAVAILABLE);
                sendTaskChannelState(
                        current, TaskResultStore.UNAVAILABLE, false, "outbox_load");
                return;
            case NEW:
                break;
            default:
                throw new IllegalStateException("unknown_task_claim_state");
        }
        sendTaskAck(current, taskId, deliveryAttemptId);
        try {
            JSONObject taskParameters = new JSONObject(parameters.toString());
            taskExecutor.execute(() -> executeTask(
                    claim.deviceScope, taskId, taskType, taskParameters, expiresAt));
        } catch (RejectedExecutionException executorRejected) {
            String durable;
            try {
                durable = resultStore.persistResult(
                        claim.deviceScope,
                        taskId,
                        errorResultMessage(taskId, "task_executor_unavailable")
                );
            } catch (RuntimeException storageFailure) {
                handleResultStoreFailure(current, storageFailure);
                return;
            }
            sendPersistedResult(
                    durable,
                    "task_executor_rejection_delivery_reconnect",
                    claim.deviceScope
            );
        }
    }

    private static String validateTaskParameters(String taskType, JSONObject parameters) {
        if (parameters == null) {
            return "task_parameters_missing";
        }
        Set<String> actual = new HashSet<>();
        Iterator<String> keys = parameters.keys();
        while (keys.hasNext()) {
            actual.add(keys.next());
        }
        Object rawSelector = parameters.opt("selector");
        JSONObject selector = rawSelector instanceof JSONObject
                ? (JSONObject) rawSelector : null;
        Set<String> selectorFields = new HashSet<>();
        if (selector != null) {
            Iterator<String> selectorKeys = selector.keys();
            while (selectorKeys.hasNext()) {
                selectorFields.add(selectorKeys.next());
            }
        }
        return CloudTaskParametersContract.validate(
                taskType,
                actual,
                parameters.opt("action"),
                parameters.opt("package"),
                selector != null,
                selectorFields,
                selector == null ? null : selector.opt("kind"),
                selector == null ? null : selector.opt("value"),
                parameters.opt("direction"),
                parameters.opt("value"),
                parameters.opt("mime_type"),
                parameters.opt("filename"),
                parameters.opt("sha256"),
                parameters.opt("data_base64")
        );
    }

    private void sendTaskAck(
            WebSocket current,
            String taskId,
            String deliveryAttemptId
    ) {
        final String encoded;
        try {
            encoded = new JSONObject()
                    .put("type", "task_ack")
                    .put("protocol_version", "1")
                    .put("task_id", taskId)
                    .put("delivery_attempt_id", deliveryAttemptId)
                    .toString();
        } catch (Exception serializationFailure) {
            CloudState.recordOutboxFailureStage(context, "outbox_replay");
            CloudState.recordConnectionError(context, "task_ack_serialization_failed");
            scheduleTaskChannelWork(current, nextTaskChannelRetrySeconds());
            return;
        }
        sendOutboxWireMessage(current, encoded, "task_ack_send_rejected");
    }

    private void executeTask(String taskScope, String taskId, String taskType,
                             JSONObject parameters, long expiresAt) {
        CloudTaskExecutor.Result result;
        long nowSeconds = trustedNowSeconds();
        long remainingVerificationMillis = TaskExecutionBudget.verificationMillis(
                expiresAt,
                nowSeconds
        );
        boolean launchAction = "action".equals(taskType) &&
                "launch_app".equals(parameters.optString("action", ""));
        if (nowSeconds >= expiresAt || (launchAction && remainingVerificationMillis == 0L)) {
            result = CloudTaskExecutor.Result.rejected("task_expired_before_execution");
        } else {
            try {
                result = executor.execute(
                        taskType,
                        parameters,
                        remainingVerificationMillis
                );
            } catch (Throwable failure) {
                result = CloudTaskExecutor.Result.failed("local_executor_unhandled_failure");
            }
        }
        String encoded;
        try {
            JSONObject message = new JSONObject()
                    .put("type", "task_result")
                    .put("protocol_version", "1")
                    .put("task_id", taskId)
                    .put("status", result.status);
            if (result.result != null) {
                message.put("result", result.result);
            }
            if (result.errorCode != null) {
                message.put("error", new JSONObject()
                        .put("code", result.errorCode)
                        .put("message", "Local allowlisted task did not complete"));
            }
            encoded = message.toString();
        } catch (Exception failure) {
            encoded = errorResultMessage(taskId, "result_serialization_failed");
        }
        try {
            String durableMessage = resultStore.persistResult(taskScope, taskId, encoded);
            sendPersistedResult(durableMessage, "task_result_delivery_reconnect", taskScope);
        } catch (RuntimeException storageFailure) {
            handleResultStoreFailure(currentSocket(), storageFailure);
        }
    }

    private boolean sendPersistedResult(String message, String reconnectReason) {
        return sendPersistedResult(message, reconnectReason, CloudState.deviceId(context));
    }

    private boolean sendPersistedResult(
            String message,
            String reconnectReason,
            String expectedScope
    ) {
        if (expectedScope == null || !expectedScope.equals(CloudState.deviceId(context))) {
            AuditLog.append(context, "task_result_preserved_for_foreign_scope", false, null);
            return false;
        }
        final WebSocket target;
        final long targetEpoch;
        synchronized (lock) {
            target = authenticatedSockets.select(socket);
            targetEpoch = target == null ? -1L : connectionEpoch;
        }
        boolean accepted = false;
        boolean websocketException = false;
        if (target != null) {
            try {
                accepted = target.send(message);
            } catch (RuntimeException sendFailure) {
                websocketException = true;
                accepted = false;
            }
        }
        final boolean confirmed;
        synchronized (lock) {
            confirmed = authenticatedSockets.deliveryConfirmed(target, accepted, socket);
        }
        if (!confirmed) {
            if (recordOutboxSendFailureForCurrent(
                    target, targetEpoch, websocketException)) {
                requestOutboxReplayReconnect(target, reconnectReason);
            }
        }
        return confirmed;
    }

    private boolean sendOutboxWireMessage(
            WebSocket current,
            String encoded,
            String reconnectReason
    ) {
        long sendEpoch = authenticatedEpoch(current);
        if (sendEpoch < 0L) {
            return false;
        }
        final boolean accepted;
        try {
            accepted = current.send(encoded);
        } catch (RuntimeException websocketFailure) {
            if (recordOutboxSendFailureForCurrent(current, sendEpoch, true)) {
                requestOutboxReplayReconnect(current, reconnectReason);
            }
            return false;
        }
        if (accepted) {
            return true;
        }
        if (recordOutboxSendFailureForCurrent(current, sendEpoch, false)) {
            requestOutboxReplayReconnect(current, reconnectReason);
        }
        return false;
    }

    private void requestOutboxReplayReconnect(WebSocket expectedSocket, String reason) {
        if (expectedSocket == null) {
            return;
        }
        postTransportEvent(() -> {
            if (!running.get() || identityRevoked || !identityBound ||
                    !CloudConfig.isConfigured()) {
                return;
            }
            if (requestSupervisorSocketRetirement(expectedSocket, reason)) {
                publishDiagnostic(() -> CloudState.recordConnectionError(context, reason));
            }
        });
    }

    private void sendRejected(WebSocket current, String taskId, String code) {
        sendResultError(current, taskId, "rejected", code);
    }

    private void persistRejectedTask(
            WebSocket current,
            String taskId,
            String deliveryAttemptId,
            TaskAdmissionPermit permit,
            String code
    ) {
        final TaskResultLedger.Claim claim;
        try {
            claim = resultStore.claim(taskId, permit);
        } catch (RuntimeException storageFailure) {
            handleResultStoreFailure(current, storageFailure);
            return;
        }
        if (claim.kind == TaskResultLedger.ClaimKind.DEADLINE_EXPIRED) {
            return;
        }
        try {
            switch (claim.kind) {
                case PENDING:
                    sendTaskAck(current, taskId, deliveryAttemptId);
                    sendPersistedResult(
                            claim.pendingMessage,
                            "task_rejection_replay_reconnect",
                            claim.deviceScope
                    );
                    return;
                case RUNNING:
                case COMPLETED:
                    sendTaskAck(current, taskId, deliveryAttemptId);
                    return;
                case CAPACITY:
                    CloudState.recordConnectionError(
                            context, "task_result_outbox_capacity");
                    CloudState.recordOutboxFailureStage(context, "outbox_load");
                    scheduleTaskChannelWork(current, 0L);
                    return;
                case NEW:
                    String durable;
                    try {
                        durable = resultStore.persistResult(
                                claim.deviceScope,
                                taskId,
                                errorResultMessage(taskId, "rejected", code)
                        );
                    } catch (RuntimeException storageFailure) {
                        handleResultStoreFailure(current, storageFailure);
                        return;
                    }
                    if (!permit.isValid()) {
                        return;
                    }
                    sendTaskAck(current, taskId, deliveryAttemptId);
                    sendPersistedResult(
                            durable,
                            "task_rejection_delivery_reconnect",
                            claim.deviceScope
                    );
                    return;
                default:
                    throw new IllegalStateException("unknown_task_claim_state");
            }
        } catch (Exception sendFailure) {
            requestOutboxReplayReconnect(current, "task_rejection_send_failed");
        }
    }


    private void sendResultError(WebSocket current, String taskId, String status, String code) {
        try {
            current.send(new JSONObject()
                    .put("type", "task_result")
                    .put("protocol_version", "1")
                    .put("task_id", taskId == null ? "" : taskId)
                    .put("status", status)
                    .put("error", new JSONObject()
                            .put("code", code)
                            .put("message", "Task rejected by Android Agent contract"))
                    .toString());
        } catch (Exception ignored) {
        }
    }

    private static String errorResultMessage(String taskId, String code) {
        return errorResultMessage(taskId, "failed", code);
    }

    private static String errorResultMessage(String taskId, String status, String code) {
        try {
            return new JSONObject()
                    .put("type", "task_result")
                    .put("protocol_version", "1")
                    .put("task_id", taskId)
                    .put("status", status)
                    .put("error", new JSONObject()
                            .put("code", code)
                            .put("message", "Local allowlisted task did not complete"))
                    .toString();
        } catch (Exception impossible) {
            throw new IllegalStateException("task_result_fallback_serialization_failed", impossible);
        }
    }

    private void handleTaskResultAck(JSONObject message) throws Exception {
        requireExactFields(message, "type", "protocol_version", "task_id");
        String taskId = boundedText(message, "task_id", 80);
        try {
            resultStore.acknowledge(taskId);
        } catch (RuntimeException storageFailure) {
            handleResultStoreFailure(currentSocket(), storageFailure);
        }
    }

    private void handleResultStoreFailure(WebSocket current, RuntimeException failure) {
        if (!running.get()) {
            if (TaskResultStore.isStorageUnavailableFailure(failure)) {
                resultStore.markUnavailable(failure);
            }
            return;
        }
        if (TaskResultStore.isStorageUnavailableFailure(failure)) {
            failResultStore(current, failure);
            return;
        }
        String code = failure instanceof TaskResultStore.StoreException
                ? ((TaskResultStore.StoreException) failure).code
                : "task_store_operation_rejected";
        CloudState.recordConnectionError(context, code);
        try {
            AuditLog.append(context, "task_store_transition_rejected", false, new JSONObject()
                    .put("classification", code));
        } catch (Exception ignored) {
        }
        CloudState.recordOutboxFailureStage(context, "outbox_replay");
        if (current != null) {
            scheduleTaskChannelWork(current, 0L);
        }
    }

    private void failResultStore(WebSocket current, RuntimeException failure) {
        resultStore.markUnavailable(failure);
        String code = resultStore.diagnosticCode();
        CloudState.recordConnectionError(context, code);
        CloudState.recordOutboxFailureStage(context, "outbox_load");
        WebSocket target = current == null ? currentSocket() : current;
        if (target != null) {
            final long epoch;
            synchronized (lock) {
                epoch = connectionEpoch;
            }
            channelLanes.updateTaskChannel(epoch, false, TaskResultStore.UNAVAILABLE);
            sendTaskChannelState(
                    target, TaskResultStore.UNAVAILABLE, false, "outbox_load");
            scheduleTaskChannelWork(target, nextTaskChannelRetrySeconds());
        }
    }

    private long trustedNowSeconds() {
        return serverClock.nowSeconds(
                SystemClock.elapsedRealtime(),
                System.currentTimeMillis() / 1000L
        );
    }

    private boolean hasUsableNetwork() {
        return usableNetwork;
    }

    private void registerNetworkCallback() {
        requireTransportActor();
        ensureNetworkLaneCapacity();
        if (networkCallbackIoLane == null) {
            scheduleNetworkRegistrationRetry(5L);
            return;
        }
        long generation = networkRegistration.begin();
        if (generation < 0L) {
            return;
        }
        final NetworkCallbackIoLane requestedLane = networkCallbackIoLane;
        boolean accepted = requestedLane.register(
                generation,
                (completedGeneration, success) -> postTransportEvent(() ->
                        completeNetworkCallbackRegistration(
                                completedGeneration, success, requestedLane)));
        if (!accepted) {
            completeNetworkCallbackRegistration(generation, false, requestedLane);
            return;
        }
        transportActor.schedule(
                () -> expireNetworkCallbackRegistration(generation, requestedLane),
                SYSTEM_IO_DEADLINE_MILLIS,
                TimeUnit.MILLISECONDS);
    }

    private void completeNetworkCallbackRegistration(
            long generation,
            boolean success,
            NetworkCallbackIoLane completedLane
    ) {
        requireTransportActor();
        if (!networkRegistration.complete(generation, success)) {
            completedLane.unregister();
            return;
        }
        if (success) {
            Boolean latestUsable = completedLane.latestUsableNetwork();
            if (latestUsable != null) {
                usableNetwork = latestUsable;
                networkStateKnown = true;
                if (latestUsable) {
                    requestSupervisorReconnectNow("network_registration_ready");
                }
            }
            return;
        }
        publishDiagnostic(() -> CloudState.recordConnectionError(
                context, "network_callback_unavailable"));
        scheduleNetworkRegistrationRetry(5L);
    }

    private void expireNetworkCallbackRegistration(
            long generation,
            NetworkCallbackIoLane timedOutLane
    ) {
        requireTransportActor();
        if (!networkRegistration.timeout(generation)) {
            return;
        }
        if (networkCallbackIoLane == timedOutLane) {
            timedOutLane.abandon();
            abandonedNetworkLanes.add(timedOutLane);
            networkCallbackIoLane = null;
            ensureNetworkLaneCapacity();
        }
        publishDiagnostic(() -> CloudState.recordConnectionError(
                context, "network_callback_registration_timeout"));
        scheduleNetworkRegistrationRetry(1L);
    }

    private void ensureNetworkLaneCapacity() {
        requireTransportActor();
        abandonedNetworkLanes.removeIf(NetworkCallbackIoLane::isStopped);
        if (networkCallbackIoLane == null &&
                abandonedNetworkLanes.size() < MAX_CONCURRENT_ABANDONED_IO_LANES) {
            networkCallbackIoLane = new NetworkCallbackIoLane(
                    context, this::onNetworkCallbackSignal);
        } else if (networkCallbackIoLane == null) {
            requestInfrastructureRecovery("network_io_rotation_exhausted");
        }
    }

    private void requestInfrastructureRecovery(String reason) {
        requireTransportActor();
        if (runtimeRestartRequested || !running.get()) {
            return;
        }
        // Queue the guardian action directly; a wedged diagnostic publisher must not suppress
        // recovery. The actor performs no Binder call—the Service main looper owns that boundary.
        RecoveryCoordinator coordinator = recoveryCoordinator;
        if (coordinator == null || !coordinator.requestInfrastructureRecovery(reason)) {
            transportActor.schedule(
                    () -> requestInfrastructureRecovery(reason), 5L, TimeUnit.SECONDS);
            return;
        }
        runtimeRestartRequested = true;
        publishDiagnostic(() -> CloudState.recordConnectionError(context, reason));
    }

    private void scheduleNetworkRegistrationRetry(long delaySeconds) {
        requireTransportActor();
        if (!running.get() || identityRevoked) {
            return;
        }
        transportActor.schedule(() -> {
            ensureNetworkLaneCapacity();
            registerNetworkCallback();
        }, delaySeconds, TimeUnit.SECONDS);
    }

    private void unregisterNetworkCallback() {
        requireTransportActor();
        networkRegistration.stopped();
        if (networkCallbackIoLane != null) {
            networkCallbackIoLane.unregister();
        }
    }

    private void onNetworkCallbackSignal(
            NetworkCallbackIoLane source,
            boolean available
    ) {
        postTransportEvent(() -> handleNetworkCallbackSignal(source, available));
    }

    private void handleNetworkCallbackSignal(
            NetworkCallbackIoLane source,
            boolean available
    ) {
        requireTransportActor();
        if (source != networkCallbackIoLane || !networkRegistration.isRegistered()) {
            return;
        }
        RecoveryCoordinator coordinator = recoveryCoordinator;
        if (coordinator != null) {
            coordinator.submitValidatedNetworkAvailability(available);
        }
        usableNetwork = available;
        networkStateKnown = true;
        if (available) {
            if (currentSocket() == null && identityBound && !identityRevoked) {
                recoveryEpisode.allowNextEpisode();
                startRecoveryEpisode(
                        SystemClock.elapsedRealtime(), "network_available");
            }
            requestSupervisorReconnectNow("network_available");
            return;
        }
        blockRecoveryEpisode(SystemClock.elapsedRealtime(), "network_unavailable");
        if (identityRevoked) {
            return;
        }
        publishDiagnostic(() -> CloudState.recordConnectionError(
                context, "network_unavailable"));
        requestSupervisorSocketRetirement("network_changed");
    }

    private void closeCurrent(String reason) {
        requireTransportActor();
        WebSocket current;
        long closedEpoch;
        CloudConnectionLifecycle.Snapshot stopped = null;
        synchronized (lock) {
            current = socket;
            closedEpoch = connectionEpoch;
            channelLanes.transportClosed(closedEpoch);
            socket = null;
            challengedSocket = null;
            authenticatedSockets.clearAll();
            serverActivityWatchdog.clearAll();
            cancelFuture(heartbeatFuture);
            cancelFuture(taskRecoveryFuture);
            heartbeatFuture = null;
            taskRecoveryFuture = null;
            socketInstalledAtElapsedMillis = NO_DEADLINE;
            supervisorRetireReason = "";
            connectionAttemptGate.clear();
            connectionEpoch++;
            if ("service_stopped".equals(reason)) {
                stopped = lifecycle.markBoundOffline(reason);
            }
        }
        if (stopped != null) {
            publishLifecycle(stopped, SystemClock.elapsedRealtime());
        }
        if (current != null) {
            final long publishedClosedEpoch = closedEpoch;
            publishDiagnostic(() -> CloudState.recordSocketRetirement(
                    context, reason, publishedClosedEpoch));
            current.close(1000, reason);
        }
    }

    private WebSocket currentSocket() {
        synchronized (lock) {
            return socket;
        }
    }

    private boolean isCurrent(WebSocket candidate) {
        synchronized (lock) {
            return socket == candidate;
        }
    }

    private boolean isAuthenticated(WebSocket candidate) {
        synchronized (lock) {
            return socket == candidate && authenticatedSockets.isAuthenticated(candidate);
        }
    }

    private static void cancelFuture(ScheduledFuture<?> future) {
        if (future != null) {
            future.cancel(false);
        }
    }

    private static String boundedText(JSONObject object, String key, int maximum) throws Exception {
        String value = object.optString(key, "").trim();
        if (value.isEmpty() || value.length() > maximum) {
            throw new IllegalArgumentException("invalid_" + key);
        }
        return value;
    }

    private static String protocolUuid(JSONObject object, String key) throws Exception {
        String value = boundedText(object, key, 80);
        try {
            if (!UUID.fromString(value).toString().equals(value)) {
                throw new IllegalArgumentException("invalid_" + key);
            }
            return value;
        } catch (IllegalArgumentException invalid) {
            throw new IllegalArgumentException("invalid_" + key, invalid);
        }
    }

    private static long boundedLong(
            JSONObject object,
            String key,
            long minimum,
            long maximum
    ) throws Exception {
        long value = object.getLong(key);
        if (value < minimum || value > maximum) {
            throw new IllegalArgumentException("invalid_" + key);
        }
        return value;
    }

    private static String optionalBoundedCode(
            JSONObject object,
            String key,
            String fallback
    ) {
        String value = object.optString(key, "");
        return value.matches("[A-Za-z0-9_.-]{1,80}") ? value : fallback;
    }

    private static String safeSocketCloseReason(int code, String reason) {
        String normalized = reason == null ? "" : reason.trim();
        if (PRESERVED_SOCKET_CLOSE_REASONS.contains(normalized)) {
            return normalized;
        }
        return "cloud_closed_" + code;
    }

    private static void requireExactFields(JSONObject object, String... fields) {
        Set<String> expected = new HashSet<>(Arrays.asList(fields));
        Set<String> actual = new HashSet<>();
        Iterator<String> keys = object.keys();
        while (keys.hasNext()) {
            actual.add(keys.next());
        }
        if (!actual.equals(expected)) {
            throw new IllegalArgumentException("unexpected_protocol_fields");
        }
    }

    private final class Listener extends WebSocketListener {
        private final long attemptEpoch;

        Listener(long attemptEpoch) {
            this.attemptEpoch = attemptEpoch;
        }

        @Override
        public void onOpen(WebSocket webSocket, Response response) {
            postTransportEvent(() -> handleOpen(webSocket));
        }

        private void handleOpen(WebSocket webSocket) {
            requireTransportActor();
            if (!installSocketForEpoch(webSocket, attemptEpoch)) {
                webSocket.cancel();
            }
        }

        @Override
        public void onMessage(WebSocket webSocket, String text) {
            postTransportEvent(() -> handleMessage(webSocket, text));
        }

        private void handleMessage(WebSocket webSocket, String text) {
            requireTransportActor();
            if (!isCurrent(webSocket) || text == null || text.length() > 1024 * 1024) {
                webSocket.close(4009, "message_too_large_or_stale_socket");
                return;
            }
            try {
                JSONObject message = new JSONObject(text);
                String type = message.optString("type", "");
                boolean authenticated = isAuthenticated(webSocket);
                if (!authenticated && !"auth_challenge".equals(type) && !"auth_ok".equals(type)) {
                    throw new IllegalStateException("message_before_auth_ok");
                }
                if (authenticated && ("auth_challenge".equals(type) || "auth_ok".equals(type))) {
                    throw new IllegalStateException("duplicate_auth_message");
                }
                switch (type) {
                    case "auth_challenge":
                        handleChallenge(webSocket, message);
                        break;
                    case "auth_ok":
                        handleAuthOk(webSocket, message);
                        break;
                    case "task":
                        dispatchTaskAdmission(webSocket, message);
                        break;
                    case "heartbeat_ack":
                        boolean hasReconciliation = message.has("outbox_reconciliation");
                        if (hasReconciliation) {
                            requireExactFields(
                                    message,
                                    "type",
                                    "protocol_version",
                                    "server_time",
                                    "outbox_reconciliation"
                            );
                        } else {
                            requireExactFields(
                                    message, "type", "protocol_version", "server_time"
                            );
                        }
                        if (!"1".equals(message.optString("protocol_version", ""))) {
                            throw new IllegalArgumentException("unsupported_protocol_version");
                        }
                        boundedLong(message, "server_time", 1L, Long.MAX_VALUE);
                        long acknowledgedAt = SystemClock.elapsedRealtime();
                        long acknowledgedEpoch;
                        CloudConnectionLifecycle.Snapshot recovered;
                        synchronized (lock) {
                            if (socket != webSocket ||
                                    !authenticatedSockets.isAuthenticated(webSocket) ||
                                    !serverActivityWatchdog.acknowledge(
                                            webSocket,
                                            acknowledgedAt
                                    )) {
                                return;
                            }
                            acknowledgedEpoch = connectionEpoch;
                            recovered = supervisorRetireReason.isEmpty()
                                    ? lifecycle.markOnlineFresh()
                                    : lifecycle.snapshot();
                            // Keep lifecycle transition and publication ordered against the
                            // supervisor's SUSPECT transition under the same socket fence.
                            publishLifecycle(recovered, acknowledgedAt);
                        }
                        final long publishedAckEpoch = acknowledgedEpoch;
                        publishDiagnostic(() -> {
                            CloudState.markHeartbeatAcknowledged(
                                    context, publishedAckEpoch);
                            CloudState.recordTransportFailureStage(context, "none");
                        });
                        if (channelLanes.isTaskChannelReady()) {
                            recoveryProgressListener.run();
                        }
                        // A retired executor may finish after the replacement socket already
                        // authenticated. Heartbeat ACK is the bounded, server-confirmed replay
                        // point for that late durable result and for a lost task_result ACK.
                        scheduleTaskChannelWork(webSocket, 0L);
                        if (hasReconciliation) {
                            scheduleOutboxReconciliation(
                                    webSocket,
                                    message.getJSONObject("outbox_reconciliation")
                            );
                        }
                        break;
                    case "task_channel_state_ack":
                        dispatchProtocolWork(
                                taskChannelScheduler,
                                webSocket,
                                () -> handleTaskChannelStateAck(webSocket, message));
                        break;
                    case "device_state_ack":
                        dispatchProtocolWork(
                                deviceStateScheduler,
                                webSocket,
                                () -> handleDeviceStateAck(webSocket, message));
                        break;
                    case "task_result_ack":
                        dispatchProtocolWork(
                                taskChannelScheduler,
                                webSocket,
                                () -> handleTaskResultAck(message));
                        break;
                    case "outbox_reconciliation_begin_ack":
                        dispatchProtocolWork(
                                taskChannelScheduler,
                                webSocket,
                                () -> handleReconciliationBeginAck(webSocket, message));
                        break;
                    case "outbox_reconciliation_item_ack":
                        dispatchProtocolWork(
                                taskChannelScheduler,
                                webSocket,
                                () -> handleReconciliationItemAck(webSocket, message));
                        break;
                    case "outbox_reconciliation_complete_ack":
                        dispatchProtocolWork(
                                taskChannelScheduler,
                                webSocket,
                                () -> handleReconciliationCompleteAck(webSocket, message));
                        break;
                    case "error":
                        String errorCode = optionalBoundedCode(message, "code", "cloud_protocol_error");
                        boolean mayPublishError;
                        synchronized (lock) {
                            mayPublishError = ConnectionCallbackFence.mayPublishAuthenticatedMessage(
                                    attemptEpoch,
                                    connectionEpoch,
                                    webSocket,
                                    socket,
                                    authenticatedSockets.isAuthenticated(webSocket)
                            );
                        }
                        if (!mayPublishError) {
                            return;
                        }
                        publishDiagnostic(() -> CloudState.recordConnectionError(
                                context, errorCode));
                        break;
                    default:
                        throw new IllegalArgumentException("unknown_message_type");
                }
            } catch (Exception protocolFailure) {
                handleProtocolFailure(webSocket, protocolFailure);
            }
        }

        @Override
        public void onClosing(WebSocket webSocket, int code, String reason) {
            postTransportEvent(() -> {
                if (isCurrent(webSocket)) {
                    webSocket.close(code, "closing_acknowledged");
                }
            });
        }

        @Override
        public void onClosed(WebSocket webSocket, int code, String reason) {
            postTransportEvent(() -> handleClosed(webSocket, code, reason));
        }

        private void handleClosed(WebSocket webSocket, int code, String reason) {
            requireTransportActor();
            boolean revoked = code == DEVICE_REVOKED_CLOSE_CODE;
            long closedAt = SystemClock.elapsedRealtime();
            long closedEpoch = -1L;
            CloudConnectionLifecycle.Snapshot reconnect = null;
            String reconnectReason = safeSocketCloseReason(code, reason);
            boolean mayReconnect = running.get() && identityBound && !identityRevoked;
            synchronized (lock) {
                if (socket != webSocket) {
                    return;
                }
                closedEpoch = connectionEpoch;
                channelLanes.transportClosed(closedEpoch);
                socket = null;
                challengedSocket = null;
                authenticatedSockets.clear(webSocket);
                serverActivityWatchdog.clear(webSocket);
                cancelFuture(heartbeatFuture);
                cancelFuture(taskRecoveryFuture);
                heartbeatFuture = null;
                taskRecoveryFuture = null;
                socketInstalledAtElapsedMillis = NO_DEADLINE;
                connectionEpoch++;
                if (!revoked && mayReconnect) {
                    lifecycle.prepareBackoffReconnect(closedAt, 0L, reconnectReason);
                    reconnect = lifecycle.enterReconnectWait(reconnectReason);
                }
            }
            if (revoked) {
                identityRevoked = true;
                blockRecoveryEpisode(closedAt, "device_revoked");
                unregisterNetworkCallback();
                publishDiagnostic(() -> {
                    CloudState.markRevoked(context);
                    AuditLog.append(context, "cloud_device_revoked", true, null);
                });
                return;
            }
            if (reconnect != null) {
                if (networkStateKnown && usableNetwork) {
                    startRecoveryEpisode(closedAt, "websocket_closed");
                    recordRecoveryFailure(closedAt, "websocket_closed");
                } else {
                    blockRecoveryEpisode(closedAt, "network_unavailable");
                }
                publishLifecycle(reconnect, closedAt);
                final long publishedClosedEpoch = closedEpoch;
                publishDiagnostic(() -> CloudState.recordSocketRetirement(
                        context, reconnectReason, publishedClosedEpoch));
                wakeSupervisor();
            }
        }

        @Override
        public void onFailure(WebSocket webSocket, Throwable throwable, Response response) {
            int httpStatus = response == null ? 0 : response.code();
            String failureStage = TransportFailureClassifier.classify(throwable, httpStatus);
            postTransportEvent(() -> handleFailure(webSocket, failureStage));
        }

        private void handleFailure(WebSocket webSocket, String failureStage) {
            requireTransportActor();
            connectionAttemptGate.finish(attemptEpoch);
            long failedAt = SystemClock.elapsedRealtime();
            long failedEpoch = -1L;
            CloudConnectionLifecycle.Snapshot reconnect = null;
            String diagnosticReason = "cloud_connection_failed";
            boolean revoked = identityRevoked;
            String previousError = identityLastError;
            synchronized (lock) {
                if (socket != webSocket) {
                    if (socket == null && connectionEpoch == attemptEpoch) {
                        failedEpoch = connectionEpoch;
                        channelLanes.transportClosed(failedEpoch);
                        connectionEpoch++;
                    } else {
                        return;
                    }
                } else {
                    failedEpoch = connectionEpoch;
                    channelLanes.transportClosed(failedEpoch);
                    socket = null;
                    challengedSocket = null;
                    authenticatedSockets.clear(webSocket);
                    serverActivityWatchdog.clear(webSocket);
                    cancelFuture(heartbeatFuture);
                    cancelFuture(taskRecoveryFuture);
                    heartbeatFuture = null;
                    taskRecoveryFuture = null;
                    socketInstalledAtElapsedMillis = NO_DEADLINE;
                    connectionEpoch++;
                }
                if (!revoked) {
                    if (previousError.startsWith("device_private_key") ||
                            previousError.startsWith("device_keystore") ||
                            "device_identity_unavailable".equals(previousError)) {
                        diagnosticReason = previousError;
                    }
                    lifecycle.prepareBackoffReconnect(failedAt, 0L, diagnosticReason);
                    reconnect = lifecycle.enterReconnectWait(diagnosticReason);
                }
            }
            if (!revoked) {
                if (networkStateKnown && usableNetwork) {
                    startRecoveryEpisode(failedAt, "transport_failure");
                    recordRecoveryFailure(failedAt, failureStage);
                } else {
                    blockRecoveryEpisode(failedAt, "network_unavailable");
                }
            }
            final String publishedDiagnosticReason = diagnosticReason;
            publishDiagnostic(() -> {
                CloudState.recordConnectionError(context, publishedDiagnosticReason);
                AuditLog.append(context, "cloud_connection_failed", false, null);
            });
            if (reconnect != null) {
                publishLifecycle(reconnect, failedAt);
                final long publishedFailedEpoch = failedEpoch;
                publishDiagnostic(() -> CloudState.recordSocketRetirement(
                        context, publishedDiagnosticReason, publishedFailedEpoch));
                wakeSupervisor();
            }
        }
    }
}
