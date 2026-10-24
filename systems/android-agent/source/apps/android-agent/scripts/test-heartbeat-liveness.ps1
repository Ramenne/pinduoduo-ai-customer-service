param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForTest = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$javac = Join-Path $javaHomeForTest 'bin\javac.exe'
$java = Join-Path $javaHomeForTest 'bin\java.exe'
$buildDir = Join-Path $projectDir 'app\build\heartbeat-liveness-test'
$production = @(
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ServerActivityWatchdog.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudChannelTiming.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\BoundedReconnectBackoff.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudConnectionLifecycle.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ConnectionAttemptGate.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ConnectionCallbackFence.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\SocketRetirementRules.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\SupervisorIterationGuard.java')
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ServiceTransportWatchdog.java')
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryEpisode.java')
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TransportFailureClassifier.java')
)
$selfTest = Join-Path $scriptDir 'HeartbeatLivenessSelfTest.java'

foreach ($required in @($javac, $java) + $production + @($selfTest)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing heartbeat liveness test dependency: $required"
    }
}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
$sources = $production + @($selfTest)
& $javac -encoding UTF-8 -d $buildDir $sources
if ($LASTEXITCODE -ne 0) {
    throw "Heartbeat liveness compilation failed with exit code $LASTEXITCODE"
}
& $java -classpath $buildDir 'com.feifei.phonebridge.HeartbeatLivenessSelfTest'
if ($LASTEXITCODE -ne 0) {
    throw "Heartbeat liveness self-test failed with exit code $LASTEXITCODE"
}
