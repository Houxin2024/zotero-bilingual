param(
    [string]$ProjectRoot = "",
    [string]$TranslatedDir = "/mnt/e/zotero-pdf2zh/server/translated",
    [string]$Python = "/mnt/e/zotero-bilingual-sync/.align-venv/bin/python",
    [string]$CacheDir = "/mnt/e/zotero-bilingual-sync/model-cache",
    [string]$StatusPath = "/mnt/e/zotero-bilingual-sync/automation-status.json",
    [string]$LogDir = "E:\zotero-pdf2zh\logs"
)

$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path $PSScriptRoot -Parent
}
$drive = $ProjectRoot.Substring(0, 1).ToLowerInvariant()
$wslProject = "/mnt/$drive/" + $ProjectRoot.Substring(3).Replace("\", "/")
$watcher = "$wslProject/backend/watch_translated.py"
$existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$watcher*" }
if ($existing) {
    exit 0
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$arguments = @(
    "-e", $Python, $watcher,
    "--translated-dir", $TranslatedDir,
    "--cache-dir", $CacheDir,
    "--status", $StatusPath,
    "--poll-seconds", "2",
    "--stable-seconds", "12"
)
Start-Process -FilePath "wsl.exe" -ArgumentList $arguments -WindowStyle Hidden | Out-Null

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    $running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$watcher*" }
    if ($running) {
        exit 0
    }
}

throw "Bilingual sidecar watcher did not start"
