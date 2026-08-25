[CmdletBinding()]
param(
    [string]$Profile,
    [string]$Destination,
    [switch]$ListProfiles,
    [string]$AdbPath,
    [string]$Serial,
    [string]$ApkPath = (Join-Path $PSScriptRoot 'kindle-kids-photo-export.apk'),
    [ValidateRange(30, 7200)]
    [int]$TimeoutSeconds = 1800,
    [switch]$KeepHelper
)

$ErrorActionPreference = 'Stop'

function Resolve-AdbPath {
    if ($AdbPath) {
        return (Resolve-Path -LiteralPath $AdbPath).Path
    }
    $command = Get-Command adb -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $sdkAdb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
    if (Test-Path -LiteralPath $sdkAdb -PathType Leaf) {
        return $sdkAdb
    }
    throw 'adb.exe was not found. Install Android SDK Platform-Tools or pass -AdbPath.'
}

function Invoke-Adb {
    param([string[]]$Arguments, [switch]$AllowFailure)
    $fullArguments = @()
    if ($script:deviceSerial -and $Arguments[0] -ne 'devices') {
        $fullArguments += @('-s', $script:deviceSerial)
    }
    $fullArguments += $Arguments
    $result = & $script:adb @fullArguments 2>&1
    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "adb failed ($exitCode): adb $($Arguments -join ' ')`n$($result -join "`n")"
    }
    return $result
}

function Install-Helper {
    if (-not (Test-Path -LiteralPath $ApkPath -PathType Leaf)) {
        throw "Helper APK not found: $ApkPath. Run .\build_apk.ps1 -PublishArtifact first."
    }
    $installed = Invoke-Adb @('shell', 'pm', 'path', 'io.github.kindlekidsphotoexport') -AllowFailure
    if ($installed -match '^package:') {
        Write-Host 'Replacing the temporary helper app...'
        Invoke-Adb @('uninstall', 'io.github.kindlekidsphotoexport') | Out-Host
    }
    Invoke-Adb @('install', '-g', $ApkPath) | Out-Host
}

$script:adb = Resolve-AdbPath
$deviceLines = Invoke-Adb @('devices')
$devices = @($deviceLines | Where-Object { $_ -match "\tdevice$" })
if ($Serial) {
    $matchingDevice = @($devices | Where-Object { ($_ -split "\t")[0] -eq $Serial })
    if ($matchingDevice.Count -ne 1) {
        throw "Authorized device serial '$Serial' was not found. Run 'adb devices'."
    }
} elseif ($devices.Count -eq 1) {
    $Serial = ($devices[0] -split "\t")[0]
} else {
    throw "Expected one authorized device or -Serial; found $($devices.Count). Run 'adb devices'."
}
$script:deviceSerial = $Serial

if ($ListProfiles) {
    Install-Helper
    Invoke-Adb @('logcat', '-c') | Out-Null
    Invoke-Adb @(
        'shell', 'am', 'start', '--user', '0', '-S',
        '-n', 'io.github.kindlekidsphotoexport/.ExportActivity',
        '--ez', 'list_profiles', 'true'
    ) | Out-Null
    Start-Sleep -Seconds 2
    $profiles = Invoke-Adb @('logcat', '-d') |
        Select-String -Pattern 'KidsPhotoExport: PROFILE_FOUND' |
        ForEach-Object { $_.Line.Substring($_.Line.IndexOf('PROFILE_FOUND')) }
    $profiles | ForEach-Object { Write-Host $_ }
    if (-not $KeepHelper) {
        Invoke-Adb @('uninstall', 'io.github.kindlekidsphotoexport') | Out-Null
    }
    return
}

if (-not $Profile -or -not $Destination) {
    throw 'Pass both -Profile and -Destination, or use -ListProfiles.'
}
if ($Profile.Contains('/') -or $Profile.Contains('..')) {
    throw 'Profile cannot contain a slash or two consecutive dots.'
}

$destinationFull = [IO.Path]::GetFullPath($Destination)
if (-not (Test-Path -LiteralPath $destinationFull)) {
    New-Item -ItemType Directory -Path $destinationFull | Out-Null
}
$safeProfile = $Profile -replace '[^A-Za-z0-9._-]', '_'
$outputName = "Kindle-Kids-Photo-Export-$safeProfile-$Serial"
$remote = "/storage/emulated/0/Download/$outputName"
$precheck = & $script:adb -s $Serial shell ls -ld $remote 2>&1
$precheckExit = $LASTEXITCODE
if ($precheckExit -eq 0) {
    throw "Tablet staging directory already exists: $remote. Preserve or remove it before retrying."
}

Install-Helper
Invoke-Adb @('logcat', '-c') | Out-Null
Invoke-Adb @(
    'shell', 'am', 'start', '--user', '0', '-S',
    '-n', 'io.github.kindlekidsphotoexport/.ExportActivity',
    '--ez', 'copy_to_download', 'true',
    '--es', 'profile_name', $Profile,
    '--es', 'output_dir', $outputName
) | Out-Null

Write-Host "Staging $Profile media on tablet $Serial..."
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$completion = $null
$abort = $null
do {
    Start-Sleep -Seconds 2
    $log = Invoke-Adb @('logcat', '-d')
    $completion = $log | Select-String -Pattern 'KidsPhotoExport: COPY_COMPLETE' |
        Select-Object -Last 1
    $abort = $log | Select-String -Pattern 'KidsPhotoExport: COPY_ABORT' |
        Select-Object -Last 1
} while (-not $completion -and -not $abort -and (Get-Date) -lt $deadline)
if ($abort) {
    throw "Tablet refused direct staging: $($abort.Line)"
}
if (-not $completion) {
    throw "Timed out. Tablet staging was preserved at $remote for diagnosis/resume."
}

$line = $completion.Line
if ($line -notmatch 'expected=(\d+) copied=(\d+) failed=(\d+) bytes=(\d+)') {
    throw "Could not parse completion record: $line"
}
$expected = [int]$Matches[1]
$copied = [int]$Matches[2]
$failed = [int]$Matches[3]
$expectedBytes = [long]$Matches[4]
if ($failed -ne 0 -or $copied -ne $expected) {
    throw "Incomplete tablet staging: expected=$expected copied=$copied failed=$failed. Preserved at $remote."
}

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
$localStage = Join-Path $tempRoot ("kindle-kids-photo-export-$Serial-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $localStage | Out-Null
$completed = $false
try {
    Write-Host "Pulling $copied files over USB..."
    Invoke-Adb @('pull', "$remote/.", $localStage) | Out-Host
    $staged = @(Get-ChildItem -LiteralPath $localStage -File)
    $stagedBytes = [long](($staged | Measure-Object Length -Sum).Sum)
    if ($staged.Count -ne $copied -or $stagedBytes -ne $expectedBytes) {
        throw "Pull verification failed: files=$($staged.Count)/$copied bytes=$stagedBytes/$expectedBytes"
    }

    $placed = 0
    $alreadyPresent = 0
    foreach ($file in $staged) {
        $target = Join-Path $destinationFull $file.Name
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            if ($sourceHash -eq $targetHash) {
                $alreadyPresent++
                continue
            }
            $target = Join-Path $destinationFull (
                $file.BaseName + '__' + $sourceHash.Substring(0, 10).ToLowerInvariant() + $file.Extension
            )
            if (Test-Path -LiteralPath $target) {
                throw "Collision target already exists: $target"
            }
        }
        Move-Item -LiteralPath $file.FullName -Destination $target
        $placed++
    }

    Invoke-Adb @('shell', 'rm', '-rf', $remote) | Out-Null
    $completed = $true
    Write-Host "COMPLETE profile=$Profile files=$copied bytes=$expectedBytes placed=$placed already_present=$alreadyPresent"
} finally {
    if ($completed) {
        $stageFull = [IO.Path]::GetFullPath($localStage)
        if (-not $stageFull.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing unsafe temporary cleanup path: $stageFull"
        }
        Remove-Item -LiteralPath $stageFull -Recurse -Force
        if (-not $KeepHelper) {
            Invoke-Adb @('uninstall', 'io.github.kindlekidsphotoexport') | Out-Null
        }
    } else {
        Write-Warning "Failure evidence preserved locally at $localStage and on the tablet at $remote"
    }
}
