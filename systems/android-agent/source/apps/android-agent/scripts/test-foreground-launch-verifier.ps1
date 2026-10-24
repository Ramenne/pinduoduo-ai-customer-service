param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForTest = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$javac = Join-Path $javaHomeForTest 'bin\javac.exe'
$java = Join-Path $javaHomeForTest 'bin\java.exe'
$buildDir = Join-Path $projectDir 'app\build\foreground-launch-verifier-test'
$production = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ForegroundLaunchVerifier.java'
$executionBudget = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\TaskExecutionBudget.java'
$selfTest = Join-Path $scriptDir 'ForegroundLaunchVerifierSelfTest.java'

foreach ($required in @($javac, $java, $production, $executionBudget, $selfTest)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing foreground launch verifier test dependency: $required"
    }
}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
& $javac -encoding UTF-8 -d $buildDir $production $executionBudget $selfTest
if ($LASTEXITCODE -ne 0) {
    throw "Foreground launch verifier compilation failed with exit code $LASTEXITCODE"
}
& $java -classpath $buildDir 'com.feifei.phonebridge.ForegroundLaunchVerifierSelfTest'
if ($LASTEXITCODE -ne 0) {
    throw "Foreground launch verifier self-test failed with exit code $LASTEXITCODE"
}
