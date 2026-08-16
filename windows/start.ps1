[CmdletBinding()]
param(
    [string]$InstallRoot = "",
    [switch]$OpenStatus,
    [int]$WaitSeconds = 45
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$layout = Get-BlrLayout -InstallRoot $InstallRoot
$config = Read-BlrConfig -Layout $layout
$port = [int]$config.port

foreach ($directory in @($layout.LogDir, $layout.StateDir, $layout.TranslatedDir, $layout.ModelCacheDir)) {
    New-BlrDirectory -Path $directory
}
if (-not (Test-Path -LiteralPath $layout.PythonExe)) {
    throw "Private Python runtime not found: $($layout.PythonExe). Run Install-Windows.cmd again."
}
$serverScript = Join-Path $layout.ServerDir "server.py"
if (-not (Test-Path -LiteralPath $serverScript)) {
    throw "PDF2zh Server not found: $($layout.ServerDir). Run Install-Windows.cmd again."
}
if (-not (Test-BlrLoopbackServerScript -Path $serverScript)) {
    throw "PDF2zh Server is not patched for loopback-only access. Run Install-Windows.cmd again before starting it."
}

$venvScripts = Join-Path $layout.VenvDir "Scripts"
$currentPath = [Environment]::GetEnvironmentVariable("Path", "Process")
$env:Path = if ([string]::IsNullOrWhiteSpace($currentPath)) {
    $venvScripts
} else {
    $venvScripts + ";" + $currentPath
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:BILINGUAL_MAPPING_STATUS = $layout.MappingStatus

$health = Test-BlrHealth -Port $port
if ($health) {
    $listener = Get-BlrListener -Port $port
    if (-not (Test-BlrLoopbackListener -Listener $listener)) {
        throw "Port $port has a healthy PDF2zh endpoint, but it is not bound only to loopback. Stop it before using this bundle."
    }
    $ownedServer = Get-BlrProcessFromPidFile `
        -PidFile $layout.ServerPid `
        -InstallRoot $layout.Root `
        -ExpectedExecutable $layout.PythonExe `
        -ExpectedCommandFragment "server.py"
    if (
        -not $ownedServer -or
        -not (Test-BlrListenerOwnedByProcessTree -Listener $listener -RootProcessId $ownedServer.ProcessId)
    ) {
        throw "Port $port is already used by another PDF2zh Server. Stop that server, or reinstall this bundle with a different -Port."
    }
    $healthVersion = Get-BlrOptionalProperty -Object $health -Name "version" -Default "unknown"
    Write-Host "PDF2zh Server is already running (version $healthVersion)."
}
else {
    $listener = Get-BlrListener -Port $port
    if ($listener) {
        throw "Port $port is occupied by PID $($listener.OwningProcess), but it is not a healthy PDF2zh Server."
    }

    Write-Host "Starting PDF2zh Server on http://127.0.0.1:$port ..."
    $serverArguments = @(
        "-X",
        "utf8",
        "server.py",
        "--enable_venv=False",
        "--check_update=False",
        "--port",
        [string]$port
    )
    $server = Start-Process `
        -FilePath $layout.PythonExe `
        -ArgumentList $serverArguments `
        -WorkingDirectory $layout.ServerDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $layout.LogDir "server.stdout.log") `
        -RedirectStandardError (Join-Path $layout.LogDir "server.stderr.log") `
        -PassThru
    Set-Content -LiteralPath $layout.ServerPid -Value $server.Id -Encoding ASCII

    $deadline = (Get-Date).AddSeconds([Math]::Max(5, $WaitSeconds))
    do {
        Start-Sleep -Milliseconds 500
        $health = Test-BlrHealth -Port $port
        if ($health) {
            break
        }
        if ($server.HasExited) {
            $stderr = Join-Path $layout.LogDir "server.stderr.log"
            $detail = if (Test-Path -LiteralPath $stderr) {
                (Get-Content -LiteralPath $stderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
            } else { "No server log was created." }
            throw "PDF2zh Server exited before becoming healthy.`n$detail"
        }
    } while ((Get-Date) -lt $deadline)
    if (-not $health) {
        throw "PDF2zh Server did not become healthy within $WaitSeconds seconds. See $($layout.LogDir)."
    }
    $listener = Get-BlrListener -Port $port
    if (-not (Test-BlrLoopbackListener -Listener $listener)) {
        Stop-BlrOwnedProcess `
            -PidFile $layout.ServerPid `
            -InstallRoot $layout.Root `
            -Label "PDF2zh Server" `
            -ExpectedExecutable $layout.PythonExe `
            -ExpectedCommandFragment "server.py"
        throw "PDF2zh Server started on a non-loopback address and was stopped for safety."
    }
    if (-not (Test-BlrListenerOwnedByProcessTree -Listener $listener -RootProcessId $server.Id)) {
        Stop-BlrOwnedProcess `
            -PidFile $layout.ServerPid `
            -InstallRoot $layout.Root `
            -Label "PDF2zh Server" `
            -ExpectedExecutable $layout.PythonExe `
            -ExpectedCommandFragment "server.py"
        throw "The healthy endpoint on port $port is not owned by the server process tree started by this bundle."
    }
    $healthVersion = Get-BlrOptionalProperty -Object $health -Name "version" -Default "unknown"
    Write-Host "PDF2zh Server is ready (version $healthVersion)."
}

$watcher = Get-BlrProcessFromPidFile `
    -PidFile $layout.WatcherPid `
    -InstallRoot $layout.Root `
    -ExpectedExecutable $layout.PythonExe `
    -ExpectedCommandFragment "watch_translated.py"
if ($watcher -and -not (Test-BlrMappingStatusFresh -Path $layout.MappingStatus -MaxAgeSeconds 300)) {
    Write-Host "Sentence-map watcher status is stale; restarting the owned watcher."
    Stop-BlrOwnedProcess `
        -PidFile $layout.WatcherPid `
        -InstallRoot $layout.Root `
        -Label "stale sentence-map watcher" `
        -ExpectedExecutable $layout.PythonExe `
        -ExpectedCommandFragment "watch_translated.py"
    $watcher = $null
}
if ($watcher) {
    Write-Host "Sentence-map watcher is already running (PID $($watcher.ProcessId))."
}
else {
    Write-Host "Starting sentence-map watcher..."
    if (Test-Path -LiteralPath $layout.MappingStatus) {
        Remove-Item -LiteralPath $layout.MappingStatus -Force
    }
    $watcherStartedAt = [DateTimeOffset]::UtcNow
    $watcherArguments = @(
        "-X",
        "utf8",
        "watch_translated.py",
        "--translated-dir",
        (ConvertTo-BlrArgument $layout.TranslatedDir),
        "--cache-dir",
        (ConvertTo-BlrArgument $layout.ModelCacheDir),
        "--status",
        (ConvertTo-BlrArgument $layout.MappingStatus),
        "--poll-seconds",
        "15",
        "--stable-seconds",
        "12"
    )
    $watcherProcess = Start-Process `
        -FilePath $layout.PythonExe `
        -ArgumentList $watcherArguments `
        -WorkingDirectory $layout.BackendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $layout.LogDir "watcher.stdout.log") `
        -RedirectStandardError (Join-Path $layout.LogDir "watcher.stderr.log") `
        -PassThru
    Set-Content -LiteralPath $layout.WatcherPid -Value $watcherProcess.Id -Encoding ASCII

    $watcherDeadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 250
        if ($watcherProcess.HasExited) {
            $stderr = Join-Path $layout.LogDir "watcher.stderr.log"
            $detail = if (Test-Path -LiteralPath $stderr) {
                (Get-Content -LiteralPath $stderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
            } else { "No watcher log was created." }
            throw "Sentence-map watcher exited before becoming ready.`n$detail"
        }
        if (
            Test-BlrMappingStatusFresh `
                -Path $layout.MappingStatus `
                -NotBefore $watcherStartedAt `
                -MaxAgeSeconds 15
        ) {
            break
        }
    } while ((Get-Date) -lt $watcherDeadline)
    if (
        $watcherProcess.HasExited -or
        -not (Test-BlrMappingStatusFresh -Path $layout.MappingStatus -NotBefore $watcherStartedAt -MaxAgeSeconds 15)
    ) {
        throw "Sentence-map watcher did not create a fresh status file while staying alive. See $($layout.LogDir)."
    }
    Write-Host "Sentence-map watcher is ready (PID $($watcherProcess.Id))."
}

Write-Host ""
Write-Host "Ready: http://127.0.0.1:$port/health" -ForegroundColor Green
Write-Host "You can now translate a PDF from Zotero."

if ($OpenStatus) {
    Start-Process "http://127.0.0.1:$port/" | Out-Null
}
