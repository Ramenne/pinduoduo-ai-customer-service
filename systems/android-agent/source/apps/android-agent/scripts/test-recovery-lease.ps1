param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForTest = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$javac = Join-Path $javaHomeForTest 'bin\javac.exe'
$java = Join-Path $javaHomeForTest 'bin\java.exe'
$buildDir = Join-Path $projectDir 'app\build\recovery-lease-test'
$production = @(
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryLease.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryLeaseStore.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryGuardianRetryPolicy.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\RecoveryAlarmIdentity.java')
)
$selfTest = Join-Path $scriptDir 'RecoveryLeaseSelfTest.java'

foreach ($required in @($javac, $java, $selfTest) + $production) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing recovery lease test dependency: $required"
    }
}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
& $javac -encoding UTF-8 -d $buildDir ($production + @($selfTest))
if ($LASTEXITCODE -ne 0) { throw "Recovery lease compilation failed: $LASTEXITCODE" }
& $java -classpath $buildDir 'com.feifei.phonebridge.RecoveryLeaseSelfTest'
if ($LASTEXITCODE -ne 0) { throw "Recovery lease self-test failed: $LASTEXITCODE" }
