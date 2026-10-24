param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForTest = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$javac = Join-Path $javaHomeForTest 'bin\javac.exe'
$java = Join-Path $javaHomeForTest 'bin\java.exe'
$buildDir = Join-Path $projectDir 'app\build\channel-separation-test'
$production = @(
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ProtocolEncoding.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CanonicalJsonWriter.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ProtocolMessages.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TransportAuthProof.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\SafeScheduledTask.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ChannelLaneState.java')
)
$selfTest = Join-Path $scriptDir 'ChannelSeparationSelfTest.java'

foreach ($required in @($javac, $java) + $production + @($selfTest)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing channel separation test dependency: $required"
    }
}

$cloudChannelPath = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudChannelClient.java'
$taskStorePath = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TaskResultStore.java'
$controlServicePath = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ControlService.java'
$cloudChannelSource = Get-Content -LiteralPath $cloudChannelPath -Raw
$taskStoreSource = Get-Content -LiteralPath $taskStorePath -Raw
$controlServiceSource = Get-Content -LiteralPath $controlServicePath -Raw

$storeConstructor = [regex]::Match(
    $taskStoreSource,
    'private TaskResultStore\(Context context\)\s*\{(?<body>.*?)\n\s*\}\s*\n\s*synchronized void prepareForCurrentScope',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $storeConstructor.Success) {
    throw 'TaskResultStore constructor structure not found'
}
if ($storeConstructor.Groups['body'].Value -match 'prepareForCurrentScope|getWritableDatabase|migrateLegacyIfNeeded') {
    throw 'TaskResultStore constructor must remain storage-I/O free'
}

$beginReconnect = [regex]::Match(
    $cloudChannelSource,
    'private String beginReconnect\(boolean replacePendingAttempt\)\s*\{(?<body>.*?)\n\s*\}\s*\n\s*private void applyReconnectFailure',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $beginReconnect.Success) {
    throw 'CloudChannelClient beginReconnect structure not found'
}
if ($beginReconnect.Groups['body'].Value -match 'resultStore|TaskResultStore|prepareForCurrentScope') {
    throw 'Transport reconnect must not enter the task store'
}

$serviceCreate = [regex]::Match(
    $controlServiceSource,
    'public void onCreate\(\)\s*\{(?<body>.*?)\n\s*\}\s*\n\s*@Override\s*\n\s*public int onStartCommand',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $serviceCreate.Success) {
    throw 'ControlService onCreate structure not found'
}
$serviceCreateBody = $serviceCreate.Groups['body'].Value
if ($serviceCreateBody -match 'TaskResultStore|prepareForCurrentScope') {
    throw 'ControlService startup must not initialize the task store'
}
if ($serviceCreateBody.IndexOf('new CloudChannelClient') -lt 0 -or
        $serviceCreateBody.IndexOf('cloudChannel.start()') -lt 0) {
    throw 'ControlService startup no longer enters the cloud transport path'
}

$challengeHandler = [regex]::Match(
    $cloudChannelSource,
    'private void handleChallenge\(WebSocket current, JSONObject message\).*?\{(?<body>.*?)\n\s*\}\s*\n\s*private void handleAuthOk',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $challengeHandler.Success) {
    throw 'CloudChannelClient handleChallenge structure not found'
}
if ($challengeHandler.Groups['body'].Value -match 'DeviceSnapshot|DeviceStateCache|TaskResultStore|resultStore') {
    throw 'Code26 transport authentication must not enter device-state or task-store code'
}
if ($challengeHandler.Groups['body'].Value -notmatch 'TransportAuthProof\.prepare') {
    throw 'Code26 transport authentication proof is not wired into handleChallenge'
}

$authOkHandler = [regex]::Match(
    $cloudChannelSource,
    'private void handleAuthOk\(WebSocket current, JSONObject message\).*?\{(?<body>.*?)\n\s*\}\s*\n\s*private void scheduleTaskChannelWork',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $authOkHandler.Success) {
    throw 'CloudChannelClient handleAuthOk structure not found'
}
$authOkBody = $authOkHandler.Groups['body'].Value
$heartbeatIndex = $authOkBody.IndexOf('startHeartbeat(current, heartbeatInterval)')
$taskIndex = $authOkBody.IndexOf('scheduleTaskChannelWork(current, 0L)')
$deviceIndex = $authOkBody.IndexOf('scheduleDeviceStateRefresh(current, 0L)')
if ($heartbeatIndex -lt 0 -or $taskIndex -le $heartbeatIndex -or
        $deviceIndex -le $heartbeatIndex) {
    throw 'Authenticated transport heartbeat must start before task/device workers'
}

$taskWorker = [regex]::Match(
    $cloudChannelSource,
    'private boolean runTaskChannelWork\(WebSocket current\)\s*\{(?<body>.*?)\n\s*\}\s*\n\s*private boolean updateTaskChannelLocalState',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $taskWorker.Success -or
        $taskWorker.Groups['body'].Value -notmatch 'resultStore\.prepareForCurrentScope\(\)') {
    throw 'Task store preparation must remain inside the independent task-channel worker'
}
if ([regex]::Matches($cloudChannelSource, 'resultStore\.prepareForCurrentScope\(\)').Count -ne 1) {
    throw 'Task store preparation must have exactly one CloudChannelClient entry point'
}

$deviceAckHandler = [regex]::Match(
    $cloudChannelSource,
    'private void handleDeviceStateAck\(WebSocket current, JSONObject message\).*?\{(?<body>.*?)\n\s*\}\s*\n\s*private void scheduleDeviceStateAckCompletion',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $deviceAckHandler.Success) {
    throw 'CloudChannelClient handleDeviceStateAck structure not found'
}
if ($deviceAckHandler.Groups['body'].Value -match 'DeviceStateCache\.store') {
    throw 'WebSocket ACK callback must not perform device-cache storage I/O'
}
if ($deviceAckHandler.Groups['body'].Value -notmatch 'scheduleDeviceStateAckCompletion') {
    throw 'Device-state ACK completion is not delegated to its worker'
}

$deviceAckWorker = [regex]::Match(
    $cloudChannelSource,
    'private void completeDeviceStateAck\(.*?\)\s*\{(?<body>.*?)\n\s*\}\s*\n\s*private boolean isAuthenticatedEpoch',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $deviceAckWorker.Success -or
        $deviceAckWorker.Groups['body'].Value -notmatch 'DeviceStateCache\.store') {
    throw 'Device cache persistence must remain inside the device-state worker'
}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
$sources = $production + @($selfTest)
& $javac -encoding UTF-8 -d $buildDir $sources
if ($LASTEXITCODE -ne 0) {
    throw "Channel separation compilation failed with exit code $LASTEXITCODE"
}
& $java -classpath $buildDir 'com.feifei.phonebridge.ChannelSeparationSelfTest'
if ($LASTEXITCODE -ne 0) {
    throw "Channel separation self-test failed with exit code $LASTEXITCODE"
}
