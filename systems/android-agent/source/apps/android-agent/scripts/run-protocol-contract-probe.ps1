param(
    [Parameter(Mandatory = $true)]
    [string]$VectorPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$output = Join-Path $projectRoot 'app\build\protocol-contract-probe'
$javaHomeForProbe = if ($env:JAVA_HOME) {
    $env:JAVA_HOME
} else {
    'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot'
}
$javac = Join-Path $javaHomeForProbe 'bin\javac.exe'
$java = Join-Path $javaHomeForProbe 'bin\java.exe'

foreach ($required in @($javac, $java, $VectorPath)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing protocol probe dependency: $required"
    }
}

New-Item -ItemType Directory -Force -Path $output | Out-Null
& $javac -encoding UTF-8 -d $output `
    (Join-Path $projectRoot 'app\src\main\java\com\feifei\phonebridge\ProtocolEncoding.java') `
    (Join-Path $projectRoot 'app\src\main\java\com\feifei\phonebridge\CanonicalJsonWriter.java') `
    (Join-Path $projectRoot 'app\src\main\java\com\feifei\phonebridge\ProtocolMessages.java') `
    (Join-Path $projectRoot 'app\src\main\java\com\feifei\phonebridge\DevicePayloadNormalizer.java') `
    (Join-Path $projectRoot 'app\src\main\java\com\feifei\phonebridge\EnrollmentProof.java') `
    (Join-Path $projectRoot 'app\src\main\java\com\feifei\phonebridge\CredentialRepairProof.java') `
    (Join-Path $projectRoot 'app\src\main\java\com\feifei\phonebridge\WebsocketAuthProof.java') `
    (Join-Path $projectRoot 'app\src\main\java\com\feifei\phonebridge\TransportAuthProof.java') `
    (Join-Path $PSScriptRoot 'ProtocolContractProbe.java')
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $java -cp $output com.feifei.phonebridge.ProtocolContractProbe $VectorPath
exit $LASTEXITCODE
