[CmdletBinding()]
param(
    [string]$AndroidSdk,
    [string]$JavaHome,
    [switch]$PublishArtifact
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot

if (-not $AndroidSdk) {
    $AndroidSdk = $env:ANDROID_SDK_ROOT
}
if (-not $AndroidSdk) {
    $AndroidSdk = $env:ANDROID_HOME
}
if (-not $AndroidSdk) {
    $AndroidSdk = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
}
if (-not (Test-Path -LiteralPath $AndroidSdk -PathType Container)) {
    throw "Android SDK not found. Pass -AndroidSdk or set ANDROID_SDK_ROOT."
}

if (-not $JavaHome) {
    $JavaHome = $env:JAVA_HOME
}
if (-not $JavaHome) {
    $studioJava = 'C:\Program Files\Android\Android Studio\jbr'
    if (Test-Path -LiteralPath $studioJava -PathType Container) {
        $JavaHome = $studioJava
    }
}
if (-not $JavaHome) {
    $javacCommand = Get-Command javac -ErrorAction SilentlyContinue
    if ($javacCommand) {
        $JavaHome = Split-Path -Parent (Split-Path -Parent $javacCommand.Source)
    }
}
if (-not $JavaHome -or -not (Test-Path -LiteralPath (Join-Path $JavaHome 'bin\javac.exe'))) {
    throw "Java JDK not found. Pass -JavaHome or install Android Studio."
}

$platform = Get-ChildItem -LiteralPath (Join-Path $AndroidSdk 'platforms') -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'android.jar') } |
    Sort-Object Name |
    Select-Object -Last 1
$buildTools = Get-ChildItem -LiteralPath (Join-Path $AndroidSdk 'build-tools') -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'apksigner.bat') } |
    Sort-Object Name |
    Select-Object -Last 1
if (-not $platform -or -not $buildTools) {
    throw "Install an Android SDK platform and Android SDK Build-Tools first."
}

$build = Join-Path $repoRoot 'build'
$rootFull = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\') + '\'
$buildFull = [IO.Path]::GetFullPath($build)
if (-not $buildFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe build path: $buildFull"
}
if (Test-Path -LiteralPath $buildFull) {
    Remove-Item -LiteralPath $buildFull -Recurse -Force
}
$classes = New-Item -ItemType Directory -Path (Join-Path $buildFull 'classes')
$dex = New-Item -ItemType Directory -Path (Join-Path $buildFull 'dex')

$androidJar = Join-Path $platform.FullName 'android.jar'
$tools = $buildTools.FullName
$javaBin = Join-Path $JavaHome 'bin'
$source = Join-Path $repoRoot 'app\src\main\java\io\github\kindlekidsphotoexport\ExportActivity.java'
$manifest = Join-Path $repoRoot 'app\src\main\AndroidManifest.xml'
$classesJar = Join-Path $buildFull 'classes.jar'
$unsigned = Join-Path $buildFull 'unsigned.apk'
$aligned = Join-Path $buildFull 'aligned.apk'
$output = Join-Path $buildFull 'kindle-kids-photo-export.apk'

function Invoke-Native {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

Invoke-Native (Join-Path $javaBin 'javac.exe') @(
    '-source', '8', '-target', '8', '-bootclasspath', $androidJar,
    '-d', $classes.FullName, $source
)
Invoke-Native (Join-Path $javaBin 'jar.exe') @(
    'cf', $classesJar, '-C', $classes.FullName, '.'
)
Invoke-Native (Join-Path $tools 'd8.bat') @(
    '--lib', $androidJar, '--min-api', '22', '--output', $dex.FullName, $classesJar
)
Invoke-Native (Join-Path $tools 'aapt.exe') @(
    'package', '-f', '-M', $manifest, '-I', $androidJar, '-F', $unsigned
)
Push-Location $dex.FullName
try {
    Invoke-Native (Join-Path $tools 'aapt.exe') @('add', $unsigned, 'classes.dex')
} finally {
    Pop-Location
}
Invoke-Native (Join-Path $tools 'zipalign.exe') @('-f', '4', $unsigned, $aligned)

$keystore = Join-Path $buildFull 'local-build.keystore'
$signingPassword = 'LocalBuild-' + [guid]::NewGuid().ToString('N')
Invoke-Native (Join-Path $javaBin 'keytool.exe') @(
    '-genkeypair', '-noprompt', '-keystore', $keystore,
    '-storepass', $signingPassword, '-keypass', $signingPassword,
    '-alias', 'localbuild', '-keyalg', 'RSA', '-keysize', '2048',
    '-validity', '10000',
    '-dname', 'CN=Local Build, OU=Kindle Kids Photo Export, O=Local, C=US'
)
Invoke-Native (Join-Path $tools 'apksigner.bat') @(
    'sign', '--ks', $keystore, '--ks-key-alias', 'localbuild',
    '--ks-pass', "pass:$signingPassword", '--key-pass', "pass:$signingPassword",
    '--out', $output, $aligned
)
Invoke-Native (Join-Path $tools 'apksigner.bat') @('verify', '--verbose', $output)

if ($PublishArtifact) {
    Copy-Item -LiteralPath $output -Destination (Join-Path $repoRoot 'kindle-kids-photo-export.apk') -Force
}

Get-FileHash -LiteralPath $output -Algorithm SHA256
Write-Host "Built $output"

