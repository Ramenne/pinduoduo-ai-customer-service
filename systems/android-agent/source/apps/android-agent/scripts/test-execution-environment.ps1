$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceDir = Join-Path (Split-Path -Parent $scriptDir) 'app\src\main\java\com\feifei\phonebridge'
$buildDir = Join-Path $scriptDir '.execution-environment-test-classes'
$javac = Join-Path $env:JAVA_HOME 'bin\javac.exe'
$java = Join-Path $env:JAVA_HOME 'bin\java.exe'

if (Test-Path -LiteralPath $buildDir) {
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
New-Item -ItemType Directory -Path $buildDir | Out-Null

& $javac -d $buildDir `
    (Join-Path $sourceDir 'ExecutionEnvironmentRules.java') `
    (Join-Path $sourceDir 'XianyuTaskAdmissionRules.java') `
    (Join-Path $scriptDir 'ExecutionEnvironmentRulesSelfTest.java') `
    (Join-Path $scriptDir 'XianyuTaskAdmissionRulesSelfTest.java')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $java -classpath $buildDir 'com.feifei.phonebridge.ExecutionEnvironmentRulesSelfTest'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $java -classpath $buildDir 'com.feifei.phonebridge.XianyuTaskAdmissionRulesSelfTest'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Remove-Item -LiteralPath $buildDir -Recurse -Force
