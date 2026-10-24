param()

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$javaHomeForTest = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot' }
$javac = Join-Path $javaHomeForTest 'bin\javac.exe'
$java = Join-Path $javaHomeForTest 'bin\java.exe'
$buildDir = Join-Path $projectDir 'app\build\cloud-task-parameters-test'
$production = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\CloudTaskParametersContract.java'
$selectorProduction = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\XianyuNodeSelector.java'
$visibilityProduction = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\VisibleNodeSearchPolicy.java'
$boundedProduction = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\BoundedXianyuActions.java'
$imageProduction = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\ImageImportContract.java'
$searchProduction = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\UniqueNodeSearch.java'
$tapProduction = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\NodeTapPolicy.java'
$boundedTest = Join-Path $scriptDir 'BoundedXianyuActionsSelfTest.java'
$descriptionProduction = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\DescriptionInputPolicy.java'
$descriptionTest = Join-Path $scriptDir 'DescriptionInputPolicySelfTest.java'
$searchInputProduction = Join-Path $projectDir 'app\src\main\java\com\feifei\phonebridge\SearchInputPolicy.java'
$searchInputTest = Join-Path $scriptDir 'SearchInputPolicySelfTest.java'
$selfTest = Join-Path $scriptDir 'CloudTaskParametersContractSelfTest.java'

foreach ($required in @($javac, $java, $production, $selectorProduction, $visibilityProduction, $selfTest, $boundedProduction, $imageProduction, $searchProduction, $tapProduction, $boundedTest, $descriptionProduction, $descriptionTest, $searchInputProduction, $searchInputTest)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing cloud task parameter test dependency: $required"
    }
}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
& $javac -encoding UTF-8 -d $buildDir $selectorProduction $visibilityProduction $boundedProduction $imageProduction $searchProduction $tapProduction $production $selfTest $boundedTest $descriptionProduction $descriptionTest $searchInputProduction $searchInputTest
if ($LASTEXITCODE -ne 0) {
    throw "Cloud task parameter compilation failed with exit code $LASTEXITCODE"
}
& $java -classpath $buildDir 'com.feifei.phonebridge.CloudTaskParametersContractSelfTest'
if ($LASTEXITCODE -ne 0) {
    throw "Cloud task parameter self-test failed with exit code $LASTEXITCODE"
}
& $java -classpath $buildDir 'com.feifei.phonebridge.BoundedXianyuActionsSelfTest'
if ($LASTEXITCODE -ne 0) {
    throw "Bounded Xianyu action self-test failed with exit code $LASTEXITCODE"
}
& $java -classpath $buildDir 'com.feifei.phonebridge.DescriptionInputPolicySelfTest'
if ($LASTEXITCODE -ne 0) { throw "Description input policy self-test failed with exit code $LASTEXITCODE" }

& $java -classpath $buildDir 'com.feifei.phonebridge.SearchInputPolicySelfTest'
if ($LASTEXITCODE -ne 0) { throw "Search input policy self-test failed with exit code $LASTEXITCODE" }
