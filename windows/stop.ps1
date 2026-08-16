[CmdletBinding()]
param([string]$InstallRoot = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$layout = Get-BlrLayout -InstallRoot $InstallRoot
Stop-BlrOwnedWatcherProcess `
    -PidFile $layout.WatcherPid `
    -WatcherScript (Join-Path $layout.BackendDir "watch_translated.py") `
    -StatusPath $layout.MappingStatus `
    -AllowedPythonRoots $layout.RuntimeDir `
    -Label "sentence-map watcher"
Stop-BlrOwnedProcess `
    -PidFile $layout.ServerPid `
    -InstallRoot $layout.Root `
    -Label "PDF2zh Server" `
    -ExpectedExecutable $layout.PythonExe `
    -ExpectedCommandFragment "server.py"
Write-Host "Zotero Bilingual Linked Reader backend is stopped."
