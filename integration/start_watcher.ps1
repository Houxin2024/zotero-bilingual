[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$InstallRoot = "",
    [string]$TranslatedDir = "",
    [string]$Python = "",
    [string]$CacheDir = "",
    [string]$StatusPath = "",
    [string]$LogDir = "",
    [string]$PidFile = "",
    [ValidateRange(1, 3600)][int]$PollSeconds = 15,
    [ValidateRange(0, 3600)][int]$StableSeconds = 12,
    [ValidateRange(1, 300)][int]$WaitSeconds = 15,
    [switch]$PassThru
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Resolve-FullPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$BasePath
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function ConvertTo-NativeArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    # A double quote is not valid in a Windows file name. Reject it here rather
    # than constructing an ambiguous command line for Windows PowerShell 5.1.
    if ($Value.Contains('"')) {
        throw "A watcher argument contains an unsupported double quote: $Value"
    }
    if ($Value -notmatch '\s') {
        return $Value
    }
    return '"' + $Value + '"'
}

function Find-PythonExecutable {
    param(
        [string]$Requested,
        [string]$Root,
        [string]$Project
    )

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        $candidates += $Requested
    }
    if (-not [string]::IsNullOrWhiteSpace($env:BLR_PYTHON)) {
        $candidates += $env:BLR_PYTHON
    }
    if (-not [string]::IsNullOrWhiteSpace($env:BILINGUAL_ALIGN_PYTHON)) {
        $candidates += $env:BILINGUAL_ALIGN_PYTHON
    }
    $candidates += @(
        (Join-Path $Root "runtime\venv\Scripts\python.exe"),
        (Join-Path $Project ".venv\Scripts\python.exe"),
        (Join-Path $Project "venv\Scripts\python.exe")
    )

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
        $command = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }

    $systemPython = Get-Command "python.exe" -CommandType Application -ErrorAction SilentlyContinue
    if ($systemPython) {
        return $systemPython.Source
    }
    throw "No native Windows Python was found. Run Install-Windows.cmd or pass -Python C:\path\to\python.exe."
}

if ($env:OS -ne "Windows_NT") {
    throw "This launcher is for native Windows. Run watch_translated.py directly on macOS or Linux."
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$watcher = Join-Path $ProjectRoot "backend\watch_translated.py"
if (-not (Test-Path -LiteralPath $watcher -PathType Leaf)) {
    throw "Sentence-map watcher not found: $watcher"
}
$common = Join-Path $ProjectRoot "windows\common.ps1"
if (-not (Test-Path -LiteralPath $common -PathType Leaf)) {
    throw "Windows lifecycle helpers not found: $common"
}
. $common

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = if (-not [string]::IsNullOrWhiteSpace($env:BLR_INSTALL_ROOT)) {
        $env:BLR_INSTALL_ROOT
    }
    else {
        $ProjectRoot
    }
}
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)

if ([string]::IsNullOrWhiteSpace($TranslatedDir)) {
    $TranslatedDir = if (-not [string]::IsNullOrWhiteSpace($env:BLR_TRANSLATED_DIR)) {
        $env:BLR_TRANSLATED_DIR
    }
    else {
        Join-Path $InstallRoot "pdf2zh-server\translated"
    }
}
if ([string]::IsNullOrWhiteSpace($CacheDir)) {
    $CacheDir = Join-Path $InstallRoot "model-cache"
}
if ([string]::IsNullOrWhiteSpace($LogDir)) {
    $LogDir = Join-Path $InstallRoot "logs"
}
if ([string]::IsNullOrWhiteSpace($PidFile)) {
    $PidFile = Join-Path $InstallRoot "state\watcher.pid"
}

$TranslatedDir = Resolve-FullPath -Path $TranslatedDir -BasePath $InstallRoot
$CacheDir = Resolve-FullPath -Path $CacheDir -BasePath $InstallRoot
if ([string]::IsNullOrWhiteSpace($StatusPath)) {
    # The unmodified PDF2zh Server can expose files from translated/ through
    # /translatedFile/. Keeping the status there lets the add-on show progress
    # without requiring a custom server endpoint.
    $StatusPath = Join-Path $TranslatedDir ".bilingual-mapping-status.json"
}
else {
    $StatusPath = Resolve-FullPath -Path $StatusPath -BasePath $InstallRoot
}
$LogDir = Resolve-FullPath -Path $LogDir -BasePath $InstallRoot
$PidFile = Resolve-FullPath -Path $PidFile -BasePath $InstallRoot
$Python = Find-PythonExecutable -Requested $Python -Root $InstallRoot -Project $ProjectRoot
$pythonOwnershipRoots = @([System.IO.Path]::GetDirectoryName($Python))
$venvRoot = Split-Path -Parent (Split-Path -Parent $Python)
$venvConfig = Join-Path $venvRoot "pyvenv.cfg"
if (Test-Path -LiteralPath $venvConfig -PathType Leaf) {
    $venvConfigText = Get-Content -LiteralPath $venvConfig -Raw -Encoding UTF8
    $homeMatch = [regex]::Match($venvConfigText, '(?m)^home\s*=\s*(?<home>.+?)\s*$')
    if ($homeMatch.Success) {
        $pythonOwnershipRoots += $homeMatch.Groups['home'].Value.Trim()
    }
}

foreach ($directory in @(
    $TranslatedDir,
    $CacheDir,
    $LogDir,
    (Split-Path -Parent $StatusPath),
    (Split-Path -Parent $PidFile)
)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$existing = Get-BlrOwnedWatcherProcess `
    -PidFile $PidFile `
    -WatcherScript $watcher `
    -StatusPath $StatusPath `
    -AllowedPythonRoots $pythonOwnershipRoots `
    -RepairPidFile
if ($existing) {
    Write-Host "Sentence-map watcher is already running (PID $($existing.ProcessId))."
    if ($PassThru) {
        $existing
    }
    exit 0
}

$previousStatusWrite = $null
if (Test-Path -LiteralPath $StatusPath) {
    $previousStatusWrite = (Get-Item -LiteralPath $StatusPath).LastWriteTimeUtc
}

$arguments = @(
    "-X",
    "utf8",
    (ConvertTo-NativeArgument $watcher),
    "--translated-dir",
    (ConvertTo-NativeArgument $TranslatedDir),
    "--cache-dir",
    (ConvertTo-NativeArgument $CacheDir),
    "--status",
    (ConvertTo-NativeArgument $StatusPath),
    "--poll-seconds",
    [string]$PollSeconds,
    "--stable-seconds",
    [string]$StableSeconds
)
$stdout = Join-Path $LogDir "watcher.stdout.log"
$stderr = Join-Path $LogDir "watcher.stderr.log"
$process = Start-Process `
    -FilePath $Python `
    -ArgumentList $arguments `
    -WorkingDirectory (Split-Path -Parent $watcher) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru
Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII

$ready = $false
$ownedWatcher = $null
$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    Start-Sleep -Milliseconds 250
    $process.Refresh()
    if (Test-Path -LiteralPath $StatusPath) {
        $statusItem = Get-Item -LiteralPath $StatusPath
        $isFresh = $null -eq $previousStatusWrite -or $statusItem.LastWriteTimeUtc -gt $previousStatusWrite
        if ($isFresh) {
            try {
                $status = Get-Content -LiteralPath $StatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
                if (-not [string]::IsNullOrWhiteSpace([string]$status.startedAt)) {
                    $ownedWatcher = Get-BlrOwnedWatcherProcess `
                        -PidFile $PidFile `
                        -WatcherScript $watcher `
                        -StatusPath $StatusPath `
                        -AllowedPythonRoots $pythonOwnershipRoots `
                        -RepairPidFile
                    $ready = $null -ne $ownedWatcher
                }
            }
            catch {
                $ready = $false
            }
        }
    }
} while (-not $ready -and (Get-Date) -lt $deadline)

if (-not $ready) {
    Stop-BlrOwnedWatcherProcess `
        -PidFile $PidFile `
        -WatcherScript $watcher `
        -StatusPath $StatusPath `
        -AllowedPythonRoots $pythonOwnershipRoots `
        -Label "failed sentence-map watcher"
    $detail = if (Test-Path -LiteralPath $stderr) {
        (Get-Content -LiteralPath $stderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
    }
    else {
        "No error log was created."
    }
    throw "Sentence-map watcher did not publish a fresh status owned by a live watcher within $WaitSeconds seconds.`n$detail"
}

Write-Host "Sentence-map watcher is ready (PID $($ownedWatcher.ProcessId))."
Write-Host "Translated PDFs: $TranslatedDir"
Write-Host "Status: $StatusPath"
if ($PassThru) {
    $ownedWatcher
}
