param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForTest = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$javac = Join-Path $javaHomeForTest 'bin\javac.exe'
$java = Join-Path $javaHomeForTest 'bin\java.exe'
$buildDir = Join-Path $projectDir 'app\build\transport-actor-contract-test'
$production = @(
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TransportEventActor.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CoalescingPublicationLane.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\PersistedOnlineStateRules.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\DeliveryAttemptContract.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\BoundedIoLane.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\NetworkCallbackRegistration.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\FailSafeStopCoordinator.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\IdentityPreflightGate.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TaskAdmissionWatchdog.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TaskAdmissionPermit.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TaskResultLedger.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ExpiredTaskAdmissionRules.java')
)
$selfTest = Join-Path $scriptDir 'TransportActorContractSelfTest.java'
$cloudChannelSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudChannelClient.java'
$controlServiceSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ControlService.java'
$taskStoreSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TaskResultStore.java'
$bootReceiverSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\BootReceiver.java'
$cloudStateSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudState.java'
$recoveryCoordinatorSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryCoordinator.java'
$recoveryGuardianSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryGuardianReceiver.java'
$recoveryLeaseStoreSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryLeaseStore.java'
$recoveryAlarmIdentitySource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryAlarmIdentity.java'
$runtimePreferencesSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\AgentRuntimePreferences.java'
$cloudTaskExecutorSource = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudTaskExecutor.java'
$buildGradleSource = Join-Path $projectDir 'app\build.gradle'
$manifestSource = Join-Path $projectDir 'app\src\main\AndroidManifest.xml'
$taskStoreSourceText = Get-Content -Raw -LiteralPath $taskStoreSource

foreach ($required in @($javac, $java) + $production + @(
    $selfTest, $cloudChannelSource, $controlServiceSource, $taskStoreSource,
    $bootReceiverSource, $cloudStateSource, $recoveryCoordinatorSource,
    $recoveryGuardianSource, $recoveryLeaseStoreSource, $recoveryAlarmIdentitySource,
    $runtimePreferencesSource, $cloudTaskExecutorSource, $buildGradleSource, $manifestSource
)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing transport actor test dependency: $required"
    }
}

# FGS construction must not synchronously touch Binder/persistence/Keystore/SQLite before the
# service has posted its foreground notification. These are source-structure contracts because
# Android framework types cannot be instantiated by this pure-Java fixture.
$cloudSource = Get-Content -Raw -LiteralPath $cloudChannelSource
$constructor = [regex]::Match(
    $cloudSource,
    'CloudChannelClient\(Context context\)\s*\{(?<body>[\s\S]*?)\n\s*\}\r?\n\r?\n\s*String start\('
)
if (-not $constructor.Success) { throw 'CloudChannelClient constructor not found' }
if ($constructor.Groups['body'].Value -match 'CloudState\.|DeviceIdentity\.|getSystemService\(|getSharedPreferences\(|prepareForCurrentScope\(|getWritableDatabase\(') {
    throw 'CloudChannelClient constructor performs external I/O'
}

$serviceSource = Get-Content -Raw -LiteralPath $controlServiceSource
$onCreate = [regex]::Match(
    $serviceSource,
    'public void onCreate\(\)\s*\{(?<body>[\s\S]*?)\n\s*\}\r?\n\r?\n\s*@Override\r?\n\s*public int onStartCommand'
)
if (-not $onCreate.Success) { throw 'ControlService.onCreate not found' }
$createBody = $onCreate.Groups['body'].Value
$foregroundIndex = $createBody.IndexOf('startForeground(')
$preferenceIndex = $createBody.IndexOf('AgentRuntimePreferences.autoConnectEnabled(')
$cloudIndex = $createBody.IndexOf('new CloudChannelClient(')
if ($foregroundIndex -lt 0 -or $preferenceIndex -lt 0 -or $cloudIndex -lt 0 -or
        $foregroundIndex -gt $preferenceIndex -or $foregroundIndex -gt $cloudIndex) {
    throw 'ControlService must enter foreground before preference/cloud bootstrap work'
}
$beforeForeground = $createBody.Substring(0, $foregroundIndex)
if ($beforeForeground -match 'AgentRuntimePreferences|CloudState\.|ProcessExitDiagnostics|BridgeState\.initialize|new HttpBridgeServer') {
    throw 'ControlService performs preference/ancillary persistence/Binder/file I/O before startForeground'
}

$storeSource = Get-Content -Raw -LiteralPath $taskStoreSource
$storeConstructor = [regex]::Match(
    $storeSource,
    'private TaskResultStore\(Context context\)\s*\{(?<body>[\s\S]*?)\n\s*\}'
)
if (-not $storeConstructor.Success) { throw 'TaskResultStore constructor not found' }
if ($storeConstructor.Groups['body'].Value -match 'getSharedPreferences\(|getWritableDatabase\(') {
    throw 'TaskResultStore construction opens persistence synchronously'
}
if ($taskStoreSourceText -notmatch 'permit == null \|\| !permit\.isValid\(\)[\s\S]*?values\.put\("state", STATE_PENDING\)[\s\S]*?ClaimKind\.DEADLINE_EXPIRED' -or
        $taskStoreSourceText -notmatch 'else \{[\s\S]*?values\.put\("state", STATE_RUNNING\)[\s\S]*?ClaimKind\.NEW') {
    throw 'task claim transaction does not atomically choose PENDING timeout or RUNNING ownership'
}

foreach ($methodName in @(
    'scheduleTaskChannelWork',
    'scheduleDeviceStateRefresh',
    'scheduleOutboxReconciliation'
)) {
    $needle = "private void $methodName("
    $methodStart = $cloudSource.IndexOf($needle, [StringComparison]::Ordinal)
    if ($methodStart -lt 0) { throw "Actor-reachable method not found: $methodName" }
    $methodEnd = $cloudSource.IndexOf("`n    private ", $methodStart + $needle.Length, [StringComparison]::Ordinal)
    if ($methodEnd -lt 0) { $methodEnd = $cloudSource.Length }
    $methodBody = $cloudSource.Substring($methodStart, $methodEnd - $methodStart)
    $catchStart = $methodBody.LastIndexOf('catch (RejectedExecutionException', [StringComparison]::Ordinal)
    if ($catchStart -lt 0) { throw "Scheduler rejection contract not found: $methodName" }
    $catchTail = $methodBody.Substring($catchStart)
    if ($catchTail -match '(?m)^ {12}(CloudState\.|AuditLog\.)') {
        throw "Actor scheduler rejection performs direct external I/O: $methodName"
    }
    if ($catchTail -notmatch 'publishDiagnostic\(') {
        throw "Actor scheduler rejection is not routed through diagnostic publication: $methodName"
    }
}

if ($cloudSource -notmatch 'startSupervisor\(\);\s*registerNetworkCallback\(\);\s*beginReconnect\(false\)') {
    throw 'network and identity startup lanes are not independently started'
}

$coordinatorSource = Get-Content -Raw -LiteralPath $recoveryCoordinatorSource
$guardianSource = Get-Content -Raw -LiteralPath $recoveryGuardianSource
$recoveryLeaseStoreText = Get-Content -Raw -LiteralPath $recoveryLeaseStoreSource
$runtimePreferencesText = Get-Content -Raw -LiteralPath $runtimePreferencesSource
$cloudTaskExecutorText = Get-Content -Raw -LiteralPath $cloudTaskExecutorSource
$buildGradleText = Get-Content -Raw -LiteralPath $buildGradleSource
$manifestText = Get-Content -Raw -LiteralPath $manifestSource
if ($coordinatorSource -notmatch 'RecoveryLeaseStore' -or
        $coordinatorSource -notmatch 'resumeGuardianClaim' -or
        $coordinatorSource -match 'newWebSocket|OkHttp|CloudState\.setConnectionState') {
    throw 'RecoveryCoordinator violates lease-only transport boundary'
}
if ($guardianSource -notmatch 'claimExpired' -or
        $guardianSource -notmatch 'requestGuardianRecovery' -or
        $guardianSource -match 'AgentRuntimePreferences|SharedPreferences|newWebSocket|OkHttp|CloudState|setConnectionState|killProcess|android\.os\.Process') {
    throw 'guardian owns transport/state or bypasses the coordinator'
}
$guardianRecovery = [regex]::Match(
    $serviceSource,
    'static GuardianRecoveryStartResult requestGuardianRecovery\(Context context,[\s\S]*?\{(?<body>[\s\S]*?)\n\s*\}\r?\n\r?\n\s*static String requestCloudReconnect'
)
if (-not $guardianRecovery.Success) {
    throw 'typed guardian recovery start contract not found'
}
$guardianRecoveryBody = $guardianRecovery.Groups['body'].Value
if ($guardianRecoveryBody -match 'AgentRuntimePreferences|SharedPreferences' -or
        $guardianRecoveryBody -notmatch 'GuardianRecoveryStartResult\.REQUESTED' -or
        $guardianRecoveryBody -notmatch 'GuardianRecoveryStartResult\.REJECTED' -or
        $guardianSource -notmatch 'result != ControlService\.GuardianRecoveryStartResult\.REQUESTED' -or
        $serviceSource -match 'guardian_recovery_disabled|guardian_recovery_requested|guardian_recovery_rejected') {
    throw 'guardian recovery still depends on process-local preferences or string failure cases'
}
if ($runtimePreferencesText -notmatch 'Application\.getProcessName\(\)' -or
        $runtimePreferencesText -notmatch 'runtime_preferences_main_process_only' -or
        $runtimePreferencesText -notmatch '!app\.getPackageName\(\)\.equals\(processName\)') {
    throw 'user runtime preferences are not guarded as main-process-only state'
}
foreach ($requiredContractSource in @(
    'AgentRuntimePreferences.java',
    'CloudChannelClient.java',
    'ControlService.java',
    'RecoveryCoordinator.java',
    'RecoveryGuardianReceiver.java',
    'RecoveryLeaseStore.java',
    'TaskResultStore.java',
    'AndroidManifest.xml'
)) {
    if (-not $buildGradleText.Contains($requiredContractSource)) {
        throw "cloud durability compatibility identity omits: $requiredContractSource"
    }
}
if ($buildGradleText -notmatch 'cloudDurabilityDigest[\s\S]*?normalizedSource[\s\S]*?replace\("\\r\\n", "\\n"\)' -or
        $buildGradleText -notmatch 'CLOUD_DURABILITY_COMPAT_ID' -or
        $buildGradleText -match 'cloudDurabilityContractFiles[\s\S]*?MainActivity\.java' -or
        $cloudTaskExecutorText -notmatch 'cloud_durability_compat_id[\s\S]*?BuildConfig\.CLOUD_DURABILITY_COMPAT_ID') {
    throw 'cloud durability source identity is not generated/reported independently of UI version'
}
if ($serviceSource -match 'coordinatedReplacement|scheduleCoordinatedReplacement|killProcess' -or
        $coordinatorSource -notmatch 'requestInfrastructureRecovery\([\s\S]*?infrastructureRecoveryReason\.compareAndSet\(null,[\s\S]*?scheduleInfrastructureRecovery\(0\);[\s\S]*?return true;' -or
        $coordinatorSource -match 'requestInfrastructureRecovery\([\s\S]{0,800}?terminalBlockRequested\.set\(true\)' -or
        $coordinatorSource -match 'claimForReplacement|EXTRA_FORCE_REPLACE|EXTRA_REPLACE_PID') {
    throw 'unproven kill/restart path remains or lane exhaustion disables guardian recovery'
}
if ($guardianSource -notmatch 'armExactAlarm\(context, claim, guardianOwner, preclaimRetries\)' -or
        $guardianSource -notmatch 'failExactGuardianHandoff' -or
        $guardianSource -notmatch 'blockExpiredMainAfterGuardianFailure' -or
        $guardianSource -notmatch 'RecoveryGuardianRetryPolicy\.mayRetry\(preclaimRetries\)' -or
        $guardianSource -notmatch 'armPreclaimRetry\(' -or
        $guardianSource -notmatch 'expireExactGuardianHandoff' -or
        $guardianSource -match 'releaseExactClaim\(' -or
        $recoveryLeaseStoreText -notmatch 'handoffDeadlineElapsed' -or
        $recoveryLeaseStoreText -notmatch 'nowElapsed > current\.deadlineElapsed' -or
        $recoveryLeaseStoreText -notmatch 'OWNER_GUARDIAN_HANDOFF_TIMEOUT') {
    throw 'guardian request acceptance is still confused with exact bounded service handoff'
}
if ($coordinatorSource -notmatch '\.setData\(Uri\.parse\(RecoveryAlarmIdentity\.dataUri\([\s\S]*?phase\)\)\)' -or
        $coordinatorSource -notmatch 'guardianIntent\(context, lease, newGuardianOwner\(trigger\), "liveness"' -or
        $guardianSource -notmatch 'guardianIntent\([\s\S]*?"handoff"' -or
        $guardianSource -notmatch 'guardianIntent\([\s\S]*?"preclaim"') {
    throw 'guardian PendingIntent identity ignores generation/process and permits stale alarm races'
}
if ($coordinatorSource -notmatch 'current\.onRecoverySignal\("guardian_lease_claimed"\);[\s\S]*?infrastructureRecoveryReason\.get\(\)[\s\S]*?scheduleInfrastructureRecovery\(0\)' -or
        $coordinatorSource -notmatch 'recordAuthenticatedProgressAndArm\([\s\S]*?armGuardian\(lease\);[\s\S]*?infrastructureRecoveryReason\.get\(\)[\s\S]*?scheduleInfrastructureRecovery\(0\)' -or
        $coordinatorSource -notmatch 'armGuardian\(requested, "infrastructure:" \+ reason\);[\s\S]*?infrastructureRecoveryConsumed\.set\(true\)') {
    throw 'temporarily claimed or blocked liveness lease can permanently lose infrastructure recovery'
}
if ($coordinatorSource -notmatch 'criticalLeaseWriter' -or
        $coordinatorSource -notmatch 'terminalBlockRequested\.set\(true\)' -or
        $coordinatorSource -notmatch 'networkAvailabilityRequested\.set\(available\)' -or
        $coordinatorSource -notmatch 'if \(terminal\)[\s\S]*?blockLease\(\s*RecoveryLease\.OWNER_TERMINAL_BLOCKED\)' -or
        $coordinatorSource -match 'leaseLane\.submit\(\(\) -> blockLease') {
    throw 'terminal/network recovery lease truth is not handled by the coalesced critical writer'
}
if ($coordinatorSource -notmatch 'terminalBlockApplied\.set\(applied\)' -or
        $coordinatorSource -notmatch 'retryCriticalWrite\s*=\s*!applied' -or
        $coordinatorSource -notmatch 'networkAvailabilityRequested\.compareAndSet\(null, false\)' -or
        $coordinatorSource -notmatch 'scheduleCriticalRetry\(\)' -or
        $coordinatorSource -notmatch 'if \(terminalBlockRequested\.get\(\)\)[\s\S]*?blockLease\(RecoveryLease\.OWNER_TERMINAL_BLOCKED\)[\s\S]*?return;' -or
        $coordinatorSource -notmatch 'Boolean\.FALSE\.equals\(latestNetworkAvailability\.get\(\)\)[\s\S]*?blockLease\(RecoveryLease\.OWNER_NETWORK_BLOCKED\)[\s\S]*?return;') {
    throw 'critical lease failure retry or stale guardian desired-state fence is missing'
}
if ($manifestText -notmatch 'android:name="\.RecoveryGuardianReceiver"[\s\S]*?android:process=":guardian"') {
    throw 'guardian receiver is not isolated in :guardian process'
}
if ($serviceSource -notmatch 'if \(!recoveryCoordinator\.attach\(cloudChannel\)\)[\s\S]*?throw new IllegalStateException\("recovery_lease_unavailable"\)' -or
        $serviceSource -notmatch 'requestGuardianRecovery\([\s\S]*?startForegroundService\(') {
    throw 'foreground service does not fail closed on lease startup or route guardian claim'
}
$supervisor = [regex]::Match(
    $cloudSource,
    'private void superviseChannel\(\)\s*\{(?<body>[\s\S]*?)\n\s*\}\r?\n\r?\n\s*private void scheduleBackoff'
)
if (-not $supervisor.Success -or
        $supervisor.Groups['body'].Value -notmatch '!networkRegistration\.isRegistered\(\)[\s\S]*registerNetworkCallback\(\)' -or
        $supervisor.Groups['body'].Value -notmatch '!identityStateKnown[\s\S]*beginReconnect\(false\)') {
    throw 'supervisor does not independently advance network and identity recovery lanes'
}
if ($cloudSource -notmatch 'requestInfrastructureRecovery\("identity_io_rotation_exhausted"\)' -or
        $cloudSource -notmatch 'requestInfrastructureRecovery\("network_io_rotation_exhausted"\)') {
    throw 'exhausted I/O lane rotation lacks controlled Service recovery'
}
if ($cloudSource -notmatch 'RecoveryCoordinator coordinator = recoveryCoordinator;\s*if \(coordinator == null \|\| !coordinator\.requestInfrastructureRecovery\(reason\)\)\s*\{[\s\S]*?requestInfrastructureRecovery\(reason\)[\s\S]*?return;[\s\S]*?runtimeRestartRequested = true;' -or
        $cloudSource -match 'publishDiagnostic\(\(\) -> \{[\s\S]{0,300}requestInfrastructureRecovery') {
    throw 'controlled runtime restart is incorrectly dependent on diagnostic publisher'
}
if ($cloudSource -notmatch 'onNetworkCallbackSignal\([\s\S]*?postTransportEvent\(\(\) -> handleNetworkCallbackSignal\(source, available\)\)' -or
        $cloudSource -notmatch 'handleNetworkCallbackSignal\([\s\S]*?if \(source != networkCallbackIoLane \|\| !networkRegistration\.isRegistered\(\)\)[\s\S]*?return;[\s\S]*?coordinator\.submitValidatedNetworkAvailability\(available\)' -or
        $coordinatorSource -match 'submitNetworkSignal\(NetworkCallbackIoLane') {
    throw 'network callback can alter recovery lease before current-lane actor validation'
}
if ($cloudSource -notmatch 'case "heartbeat_ack":[\s\S]*?channelLanes\.isTaskChannelReady\(\)[\s\S]*?recoveryProgressListener\.run\(\)' -or
        $cloudSource -notmatch 'handleTaskChannelStateAck\([\s\S]*?postTransportEvent\(\(\) -> \{[\s\S]*?isAuthenticatedEpoch\(current, readyEpoch\)[\s\S]*?completeRecoveryEpisode') {
    throw 'guardian progress is not fenced by authenticated task-channel readiness'
}

$bootSource = Get-Content -Raw -LiteralPath $bootReceiverSource
if ($bootSource -match 'AuditLog|CloudState') {
    throw 'BootReceiver performs persistence before requesting foreground Service'
}
$requestStart = [regex]::Match(
    $serviceSource,
    'static String requestStart\(Context context, String reason\)\s*\{(?<body>[\s\S]*?)\n\s*\}\r?\n\r?\n\s*static String requestCloudReconnect'
)
if (-not $requestStart.Success) { throw 'ControlService.requestStart reason overload not found' }
$requestStartBody = $requestStart.Groups['body'].Value
$startCall = $requestStartBody.IndexOf('startForegroundService(')
if ($startCall -lt 0 -or $requestStartBody.Substring(0, $startCall) -match 'CloudState\.|AuditLog') {
    throw 'requestStart performs persistence before startForegroundService'
}

$cloudStateSourceText = Get-Content -Raw -LiteralPath $cloudStateSource
$serviceRecord = [regex]::Match(
    $cloudStateSourceText,
    'static synchronized void recordServiceInstanceStarted\([\s\S]*?\n\s*\}'
)
if (-not $serviceRecord.Success -or
        $serviceRecord.Value -match 'CONNECTION_STATE|LAST_ERROR_CODE|NEXT_RECONNECT_AT|RECONNECT_ATTEMPT|RECONNECT_REASON') {
    throw 'service-start diagnostic writes transport lifecycle/reconnect truth'
}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
& $javac -encoding UTF-8 -d $buildDir ($production + @($selfTest))
if ($LASTEXITCODE -ne 0) { throw "Transport actor compilation failed: $LASTEXITCODE" }
& $java -classpath $buildDir 'com.feifei.phonebridge.TransportActorContractSelfTest'
if ($LASTEXITCODE -ne 0) { throw "Transport actor self-test failed: $LASTEXITCODE" }
