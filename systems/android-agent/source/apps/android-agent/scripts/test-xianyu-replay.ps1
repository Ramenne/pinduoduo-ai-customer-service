param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForReplay = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$androidHomeForReplay = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { 'C:\Users\developer\Android\Sdk' }
$javac = Join-Path $javaHomeForReplay 'bin\javac.exe'
$java = Join-Path $javaHomeForReplay 'bin\java.exe'
$androidJar = Join-Path $androidHomeForReplay 'platforms\android-35\android.jar'
$buildDir = Join-Path $projectDir 'app\build\xianyu-replay-test'
$sampleDir = Join-Path $projectDir 'testdata'

foreach ($required in @($javac, $java, $androidJar, $sampleDir)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing replay dependency: $required"
    }
}
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null

$pluginDir = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\plugin\xianyu'
$messagingPluginDir = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\plugin\messaging'
$executorPath = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudTaskExecutor.java'
$sources = @(
    (Join-Path $messagingPluginDir 'MessagingPlatformAdapter.java'),
    (Join-Path $messagingPluginDir 'MessagingSnapshotContract.java'),
    (Join-Path $messagingPluginDir 'MessagingPlatformRegistry.java'),
    (Join-Path $pluginDir 'UiNode.java'),
    (Join-Path $pluginDir 'XianYuPage.java'),
    (Join-Path $pluginDir 'ItemCardExtractor.java'),
    (Join-Path $pluginDir 'MessageExtractor.java'),
    (Join-Path $pluginDir 'MessageHistoryMerger.java'),
    (Join-Path $pluginDir 'XianyuHistoryState.java'),
    (Join-Path $pluginDir 'XianyuNavigationSemantics.java'),
    (Join-Path $pluginDir 'XianyuSnapshotRetryPolicy.java'),
    (Join-Path $pluginDir 'XianYuPlugin.java'),
    (Join-Path $pluginDir 'XianyuSnapshotCapture.java'),
    (Join-Path $scriptDir 'XianYuReplaySelfTest.java')
)

# Board 1 boundary guard: the Board 0 cloud task may take exactly one accessibility
# snapshot and pass that immutable JSON into the Xianyu recognizer. It must not
# grow an action, shell, network, or Android service dependency unnoticed.
$executorSource = Get-Content -LiteralPath $executorPath -Raw
$snapshotBranch = [regex]::Match(
    $executorSource,
    '(?s)case\s+"xianyu_snapshot"\s*:(?<body>.*?)\s*default\s*:'
)
if (-not $snapshotBranch.Success) {
    throw 'Missing xianyu_snapshot cloud task branch'
}
$snapshotBody = $snapshotBranch.Groups['body'].Value
if ($snapshotBody -notmatch 'XianyuSnapshotCapture\.capture\s*\(' -or
        $snapshotBody -notmatch 'xianyuService::observe') {
    throw 'xianyu_snapshot must use the bounded read-only snapshot capture helper'
}
$snapshotCapturePath = Join-Path $pluginDir 'XianyuSnapshotCapture.java'
$snapshotCaptureSource = Get-Content -LiteralPath $snapshotCapturePath -Raw
if (($snapshotCaptureSource | Select-String -AllMatches 'observer\.observe\s*\(').Matches.Count -ne 2 -or
        $snapshotCaptureSource -match '\b(?:for|while)\s*\(') {
    throw 'Xianyu snapshot capture must contain exactly two static observe sites and no retry loop'
}
$forbiddenBoundaryPatterns = @(
    '\.performAction\s*\(',
    '\.dispatchGesture\s*\(',
    'Runtime\.getRuntime\s*\(',
    'new\s+ProcessBuilder\s*\(',
    'java\.net\.',
    'okhttp3\.',
    'getLaunchIntentForPackage\s*\(',
    '\.startActivity\s*\(',
    'BridgeAccessibilityService\.(click|input|swipe|launch)'
)
$boundarySource = $snapshotBody + "`n" + ((Get-ChildItem -LiteralPath $pluginDir -Filter '*.java' |
        ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw }) -join "`n")
foreach ($pattern in $forbiddenBoundaryPatterns) {
    if ($boundarySource -match $pattern) {
        throw "Xianyu read-only boundary contains forbidden capability pattern: $pattern"
    }
}

& $javac -encoding UTF-8 -classpath $androidJar -d $buildDir @sources
if ($LASTEXITCODE -ne 0) {
    throw "Replay Java compilation failed with exit code $LASTEXITCODE"
}

function ConvertTo-Base64([string]$value) {
    if ($null -eq $value) { $value = '' }
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($value))
}

function ConvertFrom-Base64([string]$value) {
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($value))
}

$expectedPages = @{
    'xianyu_01_launch.json' = 'HOME'
    'xianyu_02_messages.json' = 'LOGIN'
    'xianyu_03_home.json' = 'HOME'
    'xianyu_04_detail.json' = 'LOGIN'
    'xianyu_05_home_deep.json' = 'LOGIN'
    'xianyu_06_home.json' = 'LOGIN'
    'xianyu_07.json' = 'HOME'
}
$expectedAnchors = @(263, 276, 288)
$expectedTitleNodes = @(262, 274, 286)
$expectedSellerNodes = @(269, 281, 293)
$expectedPrices = @('1380', '350', '89')
$failures = [Collections.Generic.List[string]]::new()
$results = [Collections.Generic.List[object]]::new()

foreach ($sampleName in ($expectedPages.Keys | Sort-Object)) {
    $samplePath = Join-Path $sampleDir $sampleName
    $snapshot = Get-Content -LiteralPath $samplePath -Raw | ConvertFrom-Json
    $lines = [Collections.Generic.List[string]]::new()
    $lines.Add("META`t$(ConvertTo-Base64 ([string]$snapshot.package))")
    foreach ($node in @($snapshot.nodes)) {
        $bounds = @($node.bounds)
        if ($bounds.Count -ne 4) { $bounds = @(0, 0, 0, 0) }
        $fields = @(
            'NODE',
            [string]$node.index,
            [string]$node.depth,
            (ConvertTo-Base64 ([string]$node.class)),
            (ConvertTo-Base64 ([string]$node.resource_id)),
            (ConvertTo-Base64 ([string]$node.text)),
            (ConvertTo-Base64 ([string]$node.description)),
            ([bool]$node.clickable).ToString().ToLowerInvariant(),
            ([bool]$node.editable).ToString().ToLowerInvariant(),
            ([bool]$node.scrollable).ToString().ToLowerInvariant(),
            ([bool]$node.enabled).ToString().ToLowerInvariant(),
            [string]$bounds[0], [string]$bounds[1], [string]$bounds[2], [string]$bounds[3]
        )
        $lines.Add($fields -join "`t")
    }
    $tsvPath = Join-Path $buildDir ($sampleName + '.tsv')
    [IO.File]::WriteAllLines($tsvPath, $lines, [Text.UTF8Encoding]::new($false))

    $classPath = "$buildDir;$androidJar"
    $output = @(& $java -classpath $classPath 'com.feifei.phonebridge.plugin.xianyu.XianYuReplaySelfTest' $tsvPath)
    if ($LASTEXITCODE -ne 0) {
        $failures.Add("$sampleName Java replay failed with exit code $LASTEXITCODE")
        continue
    }
    $pageParts = @($output | Where-Object { $_ -like "PAGE`t*" } | Select-Object -First 1) -split "`t"
    $summaryParts = @($output | Where-Object { $_ -like "SUMMARY`t*" } | Select-Object -First 1) -split "`t"
    $page = if ($pageParts.Count -ge 2) { $pageParts[1] } else { '' }
    $cardCount = if ($summaryParts.Count -ge 3) { [int]$summaryParts[1] } else { -1 }
    $incompleteCount = if ($summaryParts.Count -ge 3) { [int]$summaryParts[2] } else { -1 }
    if ($page -ne $expectedPages[$sampleName]) {
        $failures.Add("$sampleName expected page $($expectedPages[$sampleName]), got $page")
    }

    $cardLines = @($output | Where-Object { $_ -like "CARD`t*" })
    if ($page -eq 'HOME') {
        if ($cardCount -ne 3 -or $incompleteCount -ne 1) {
            $failures.Add("$sampleName expected 3 complete + 1 incomplete cards, got $cardCount + $incompleteCount")
        }
        $nodesByIndex = @{}
        foreach ($node in @($snapshot.nodes)) { $nodesByIndex[[int]$node.index] = $node }
        $cardsByAnchor = @{}
        foreach ($line in $cardLines) {
            $parts = $line -split "`t"
            if ($parts.Count -eq 5) { $cardsByAnchor[[int]$parts[1]] = $parts }
        }
        for ($i = 0; $i -lt $expectedAnchors.Count; $i++) {
            $anchor = $expectedAnchors[$i]
            if (-not $cardsByAnchor.ContainsKey($anchor)) {
                $failures.Add("$sampleName missing complete card at anchor $anchor")
                continue
            }
            $actual = $cardsByAnchor[$anchor]
            $expectedTitle = ([string]$nodesByIndex[$expectedTitleNodes[$i]].text).Trim()
            if ($expectedTitle.StartsWith('pic')) { $expectedTitle = $expectedTitle.Substring(3).Trim() }
            $expectedSeller = ([string]$nodesByIndex[$expectedSellerNodes[$i]].text).Trim()
            if ((ConvertFrom-Base64 $actual[2]) -ne $expectedTitle) {
                $failures.Add("$sampleName anchor $anchor title mismatch")
            }
            if ($actual[3] -ne $expectedPrices[$i]) {
                $failures.Add("$sampleName anchor $anchor expected price $($expectedPrices[$i]), got $($actual[3])")
            }
            if ((ConvertFrom-Base64 $actual[4]) -ne $expectedSeller) {
                $failures.Add("$sampleName anchor $anchor seller mismatch")
            }
        }
    } elseif ($cardCount -ne 0 -or $incompleteCount -ne 0 -or $cardLines.Count -ne 0) {
        $failures.Add("$sampleName must not run item extraction outside HOME")
    }
    $results.Add([pscustomobject]@{
        Sample = $sampleName
        Page = $page
        CompleteCards = $cardCount
        IncompleteCards = $incompleteCount
    })
}

$results | Format-Table -AutoSize
if ($failures.Count -gt 0) {
    throw ("XianYu replay failed:`n - " + ($failures -join "`n - "))
}
Write-Output 'XianYu replay: PASS (7 samples + guards + cloud snapshot read-only boundary)'
