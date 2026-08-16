[CmdletBinding()]
param([string]$InstallRoot = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$layout = Get-BlrLayout -InstallRoot $InstallRoot
$config = Read-BlrConfig -Layout $layout
$port = [int]$config.port
$health = Test-BlrHealth -Port $port
$listener = Get-BlrListener -Port $port
$serverLoopbackOnly = Test-BlrLoopbackListener -Listener $listener
$server = Get-BlrProcessFromPidFile `
    -PidFile $layout.ServerPid `
    -InstallRoot $layout.Root `
    -ExpectedExecutable $layout.PythonExe `
    -ExpectedCommandFragment "server.py"
$serverListenerOwned = if ($server) {
    Test-BlrListenerOwnedByProcessTree -Listener $listener -RootProcessId $server.ProcessId
} else {
    $false
}
$watcher = Get-BlrProcessFromPidFile `
    -PidFile $layout.WatcherPid `
    -InstallRoot $layout.Root `
    -ExpectedExecutable $layout.PythonExe `
    -ExpectedCommandFragment "watch_translated.py"
$watcherStatusFresh = Test-BlrMappingStatusFresh -Path $layout.MappingStatus -MaxAgeSeconds 300

Write-Host "Install root: $($layout.Root)"
if ($health -and $serverLoopbackOnly -and $serverListenerOwned) {
    $healthVersion = Get-BlrOptionalProperty -Object $health -Name "version" -Default "unknown"
    Write-Host "PDF2zh Server: RUNNING (PID $($server.ProcessId), version $healthVersion, loopback port $port)" -ForegroundColor Green
}
elseif ($health) {
    if (-not $serverLoopbackOnly) {
        Write-Host "PDF2zh Server: UNSAFE LISTENER (port $port is not loopback-only)" -ForegroundColor Red
    }
    else {
        Write-Host "PDF2zh Server: UNOWNED ENDPOINT (port $port belongs to another process)" -ForegroundColor Red
    }
}
else {
    Write-Host "PDF2zh Server: STOPPED or unhealthy (port $port)" -ForegroundColor Red
}

if ($watcher -and $watcherStatusFresh) {
    Write-Host "Sentence-map watcher: RUNNING (PID $($watcher.ProcessId))" -ForegroundColor Green
}
elseif ($watcher) {
    Write-Host "Sentence-map watcher: RUNNING but status is stale (PID $($watcher.ProcessId))" -ForegroundColor Red
}
else {
    Write-Host "Sentence-map watcher: STOPPED" -ForegroundColor Red
}

if (Test-Path -LiteralPath $layout.MappingStatus) {
    try {
        $status = Get-Content -LiteralPath $layout.MappingStatus -Raw -Encoding UTF8 | ConvertFrom-Json
        $mappingState = Get-BlrOptionalProperty -Object $status -Name "state" -Default "unknown"
        $currentFile = Get-BlrOptionalProperty -Object $status -Name "currentFile" -Default ""
        $mappingProgress = Get-BlrOptionalProperty -Object $status -Name "mappingProgress"
        $updatedAt = Get-BlrOptionalProperty -Object $status -Name "updatedAt" -Default "unknown"
        Write-Host "Mapping state: $mappingState"
        if (-not [string]::IsNullOrWhiteSpace([string]$currentFile)) {
            Write-Host "Current file: $currentFile"
        }
        if ($null -ne $mappingProgress) {
            Write-Host "Mapping progress: $mappingProgress%"
        }
        Write-Host "Status updated: $updatedAt"
    }
    catch {
        Write-Host "Mapping status file exists but could not be read: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

Write-Host "Logs: $($layout.LogDir)"
if (
    -not $health -or
    -not $serverLoopbackOnly -or
    -not $serverListenerOwned -or
    -not $watcher -or
    -not $watcherStatusFresh
) {
    exit 1
}
