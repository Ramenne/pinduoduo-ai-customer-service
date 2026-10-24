$ErrorActionPreference = 'Stop'

function Decode-Utf8([string]$encoded) {
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$androidRoot = Split-Path -Parent $scriptDir
$repoRoot = Split-Path -Parent (Split-Path -Parent $androidRoot)
$mainActivityPath = Join-Path $androidRoot 'app\src\main\java\com\feifei\phonebridge\MainActivity.java'
$controlServicePath = Join-Path $androidRoot 'app\src\main\java\com\feifei\phonebridge\ControlService.java'
$runtimePreferencesPath = Join-Path $androidRoot 'app\src\main\java\com\feifei\phonebridge\AgentRuntimePreferences.java'
$manifestPath = Join-Path $androidRoot 'app\src\main\AndroidManifest.xml'
$sourceRoot = Join-Path $androidRoot 'app\src\main\java'
$pagePath = Join-Path $repoRoot 'services\control-plane\src\oin_control_plane\pages.py'
$utf8 = [Text.UTF8Encoding]::new($false)
$mainActivity = [IO.File]::ReadAllText($mainActivityPath, $utf8)
$controlService = [IO.File]::ReadAllText($controlServicePath, $utf8)
$runtimePreferences = [IO.File]::ReadAllText($runtimePreferencesPath, $utf8)
$manifest = [IO.File]::ReadAllText($manifestPath, $utf8)
$pages = [IO.File]::ReadAllText($pagePath, $utf8)

$requiredVisible = @(
    'T0lOIOWuouacjeWKqeaJiw==',
    '55m75b2V6LSm5Y+3',
    '6K6+5aSH5ZCN77ya',
    '5L+u5pS5',
    '5byA5ZCv5peg6Zqc56KN5pyN5Yqh',
    '6Ieq5Yqo6L+e5o6l5pyN5Yqh5Zmo',
    '5L2/55So546v5aKD',
    '5pyN5Yqh54q25oCB77ya',
    '5YaF5qC454mI5pys77ya',
    '5a6J5Y2T54mI5pys77ya',
    'Uk9PVCDmnYPpmZDvvJo=',
    '5o6o6I2Q77ya',
    '5pyN5Yqh5Zmo6L+e5o6l5q2j5bi4',
    '5q2j5Zyo6L+e5o6l5pyN5Yqh5Zmo',
    '5pyN5Yqh5Zmo5pyq6L+e5o6l',
    '5b2T5YmN5L2/55So546v5aKD5q2j5bi4'
) | ForEach-Object { Decode-Utf8 $_ }
foreach ($required in $requiredVisible + @(
    'Settings.ACTION_ACCESSIBILITY_SETTINGS',
    'ControlService.setAutoConnectEnabled',
    'DevicePreferences.setDisplayName',
    'ControlService.isCloudChannelFresh',
    'Build.VERSION.RELEASE',
    'DeviceSnapshot.hasRootSignal',
    'handler.removeCallbacks(refreshTicker)'
)) {
    if (-not $mainActivity.Contains($required)) {
        throw "missing required device-shell behavior: $required"
    }
}

$forbiddenVisible = @(
    '5pys5py66Zey6bG86Ieq5qOA',
    '6YeN5paw5bu656uL5LqR56uv6L+e5o6l',
    '5by65YaZ5qih5byP',
    '6K+t6Z+z6K+G5Yir',
    '6Zey6bG8IEFJIOWuouacjeiuvuWkh+errw==',
    '5pyA6L+R5LqR56uv56Gu6K6k',
    '6Zey6bG85omn6KGM546v5aKD',
    '5ZCO5Y+w6L+Q6KGM',
    '57Sn5oCl5pqC5YGc',
    '56uL5Y2z5pqC5YGc',
    '5b2T5YmN5Li757q/',
    'T0NS6K+G5Zu+',
    '5oKs5rWu56qX5p2D6ZmQ'
) | ForEach-Object { Decode-Utf8 $_ }
foreach ($forbidden in $forbiddenVisible + @(
    'LocalXianyuSelfCheck',
    'server_session_id',
    'socket_epoch',
    'bridge_token',
    'ScrollView'
)) {
    if ($mainActivity.Contains($forbidden)) {
        throw "forbidden phone-shell text or diagnostic detail: $forbidden"
    }
}

foreach ($required in @(
    'static String setAutoConnectEnabled',
    'AgentRuntimePreferences.setAutoConnectEnabled',
    'app.stopService',
    'auto_connect_disabled',
    'AgentRuntimePreferences.autoConnectEnabled'
)) {
    if (-not $controlService.Contains($required)) {
        throw "auto-connect runtime control is incomplete: $required"
    }
}

if (-not $runtimePreferences.Contains('getBoolean(AUTO_CONNECT, true)') -or
    -not $runtimePreferences.Contains('.commit()') -or
    -not $runtimePreferences.Contains('Application.getProcessName()') -or
    -not $runtimePreferences.Contains('runtime_preferences_main_process_only')) {
    throw 'auto-connect preference is not durable, default-on and main-process-only'
}

foreach ($forbiddenCapability in @(
    'android.permission.SYSTEM_ALERT_WINDOW',
    'Settings.ACTION_MANAGE_OVERLAY_PERMISSION',
    'Settings.canDrawOverlays'
)) {
    if ($manifest.Contains($forbiddenCapability) -or $mainActivity.Contains($forbiddenCapability)) {
        throw "unused overlay capability was added: $forbiddenCapability"
    }
}

$localSelfCheckReference = Get-ChildItem -LiteralPath $sourceRoot -Filter '*.java' -Recurse |
    Select-String -Pattern 'LocalXianyuSelfCheck|ENABLE_LOCAL_XIANYU_SELF_CHECK'
if ($localSelfCheckReference) {
    throw 'local Xianyu self-check source references remain'
}

$cloudTestHeading = '<h2>' + (Decode-Utf8 '6K6+5aSH5rWL6K+V77yI5LqR56uv77yJ') + '</h2>'
$cloudOnlyText = Decode-Utf8 '5omL5py656uv5LiN5YaN5pS+572u5pys5py66Ieq5qOA'
if (-not $pages.Contains($cloudTestHeading) -or -not $pages.Contains($cloudOnlyText)) {
    throw 'cloud device-test section is missing'
}

Write-Output 'Device shell scope self-test PASS'
