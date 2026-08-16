[CmdletBinding()]
param([string]$InstallRoot = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$layout = Get-BlrLayout -InstallRoot $InstallRoot
Stop-BlrOwnedProcess `
    -PidFile $layout.WatcherPid `
    -InstallRoot $layout.Root `
    -Label "sentence-map watcher" `
    -ExpectedExecutable $layout.PythonExe `
    -ExpectedCommandFragment "watch_translated.py"
Stop-BlrOwnedProcess `
    -PidFile $layout.ServerPid `
    -InstallRoot $layout.Root `
    -Label "PDF2zh Server" `
    -ExpectedExecutable $layout.PythonExe `
    -ExpectedCommandFragment "server.py"
Write-Host "Zotero Bilingual Linked Reader backend is stopped."
