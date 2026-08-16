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

function Stop-StartedProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    # A Windows venv python.exe can be a redirector which owns the real base
    # Python as a child process. Kill the known process tree on failed startup
    # so that child cannot survive as an untracked watcher.
    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    if (Test-Path -LiteralPath $taskkill -PathType Leaf) {
        & $taskkill /PID ([string]$ProcessId) /T /F 2>$null | Out-Null
        return
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Get-OwnedWatcher {
    param(
        [string]$WatcherPath,
        [string]$InstancePath,
        [string]$WatcherPidFile
    )

    if (Test-Path -LiteralPath $WatcherPidFile) {
        $raw = ([string](Get-Content -LiteralPath $WatcherPidFile -Raw -ErrorAction SilentlyContinue)).Trim()
        $processId = 0
        if ([int]::TryParse($raw, [ref]$processId)) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
            if ($process -and $process.CommandLine -and
                $process.CommandLine.IndexOf($WatcherPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                $process.CommandLine.IndexOf($InstancePath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $process
            }
        }
        Remove-Item -LiteralPath $WatcherPidFile -Force -ErrorAction SilentlyContinue
    }

    $existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.IndexOf($WatcherPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $_.CommandLine.IndexOf($InstancePath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        Select-Object -First 1
    if ($existing) {
        Set-Content -LiteralPath $WatcherPidFile -Value $existing.ProcessId -Encoding ASCII
    }
    return $existing
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

foreach ($directory in @(
    $TranslatedDir,
    $CacheDir,
    $LogDir,
    (Split-Path -Parent $StatusPath),
    (Split-Path -Parent $PidFile)
)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$existing = Get-OwnedWatcher `
    -WatcherPath $watcher `
    -InstancePath $StatusPath `
    -WatcherPidFile $PidFile
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
$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    Start-Sleep -Milliseconds 250
    $process.Refresh()
    if ($process.HasExited) {
        $detail = if (Test-Path -LiteralPath $stderr) {
            (Get-Content -LiteralPath $stderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        }
        else {
            "No error log was created."
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        throw "Sentence-map watcher exited before becoming ready.`n$detail"
    }
    if (Test-Path -LiteralPath $StatusPath) {
        $statusItem = Get-Item -LiteralPath $StatusPath
        $isFresh = $null -eq $previousStatusWrite -or $statusItem.LastWriteTimeUtc -gt $previousStatusWrite
        if ($isFresh) {
            try {
                $status = Get-Content -LiteralPath $StatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $ready = -not [string]::IsNullOrWhiteSpace([string]$status.startedAt)
            }
            catch {
                $ready = $false
            }
        }
    }
} while (-not $ready -and (Get-Date) -lt $deadline)

if (-not $ready) {
    Stop-StartedProcessTree -ProcessId $process.Id
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    throw "Sentence-map watcher did not publish a fresh status within $WaitSeconds seconds. See $stderr."
}

Write-Host "Sentence-map watcher is ready (PID $($process.Id))."
Write-Host "Translated PDFs: $TranslatedDir"
Write-Host "Status: $StatusPath"
if ($PassThru) {
    $process
}
