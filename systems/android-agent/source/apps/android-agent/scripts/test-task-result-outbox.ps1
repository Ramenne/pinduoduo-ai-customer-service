param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForTest = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$javac = Join-Path $javaHomeForTest 'bin\javac.exe'
$java = Join-Path $javaHomeForTest 'bin\java.exe'
$buildDir = Join-Path $projectDir 'app\build\task-result-outbox-test'
$production = @(
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TaskResultLedger.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\AuthenticatedSocketGate.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ReconnectPreflight.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TrustedServerClock.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\Utf8JsonBudget.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TaskStoreRules.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ActivePublisherGate.java')
)
$selfTest = Join-Path $scriptDir 'TaskResultLedgerSelfTest.java'

foreach ($required in @($javac, $java) + $production + @($selfTest)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing outbox test dependency: $required"
    }
}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
& $javac -encoding UTF-8 -d $buildDir @production $selfTest
if ($LASTEXITCODE -ne 0) {
    throw "Task result outbox compilation failed with exit code $LASTEXITCODE"
}
& $java -classpath $buildDir 'com.feifei.phonebridge.TaskResultLedgerSelfTest'
if ($LASTEXITCODE -ne 0) {
    throw "Task result outbox test failed with exit code $LASTEXITCODE"
}
