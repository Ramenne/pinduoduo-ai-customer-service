param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForTest = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$javac = Join-Path $javaHomeForTest 'bin\javac.exe'
$java = Join-Path $javaHomeForTest 'bin\java.exe'
$buildDir = Join-Path $projectDir 'app\build\durable-binding-test'
$sources = @(
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\BoundedReconnectBackoff.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudConnectionLifecycle.java'),
    (Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\DurableBindingRules.java'),
    (Join-Path $scriptDir 'DurableBindingRulesSelfTest.java')
)

foreach ($required in @($javac, $java) + $sources) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing durable binding test dependency: $required"
    }
}
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
& $javac -encoding UTF-8 -d $buildDir $sources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $java -classpath $buildDir 'com.feifei.phonebridge.DurableBindingRulesSelfTest'
exit $LASTEXITCODE
