[CmdletBinding()]
param(
    [string]$InstallRoot = "",
    [ValidateRange(1024, 65535)][int]$Port = 8890,
    [switch]$EnableAutoStart,
    [switch]$NoAutoStart,
    [switch]$NoStart,
    [switch]$NoShortcuts,
    [switch]$NoOpenAddons,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$Pdf2zhVersion = "4.0.3"
$Pdf2zhServerUrl = "https://github.com/guaguastandup/zotero-pdf2zh/releases/download/v4.0.3/server.zip"
$Pdf2zhServerSha256 = "9a125fb1a4d16029d297bc3691b02282670c60cd91db098e45e029691e407b69"
$Pdf2zhAddonUrl = "https://github.com/guaguastandup/zotero-pdf2zh/releases/download/v4.0.3/zotero-pdf-2-zh.xpi"
$Pdf2zhAddonSha256 = "31a7d73f67096dcfd1640012cad391a8898da78aef62782b91bb9b9f153cd8fc"
$UvVersion = "0.12.5"
$UvArchiveUrl = "https://github.com/astral-sh/uv/releases/download/0.12.5/uv-x86_64-pc-windows-msvc.zip"
$UvArchiveSha256 = "4c4d49d8738847d9b71ba319e49a5688c93eac0fe6204b1df24e98528dddf39a"
$Pdf2zhNextRequirement = "pdf2zh-next==2.9.0"
$AlignmentModel = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
$AlignmentModelRepo = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
$AlignmentModelRevision = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
$DependencyCutoff = "2026-08-16T23:59:59Z"

if ($EnableAutoStart -and $NoAutoStart) {
    throw "Use either -EnableAutoStart or -NoAutoStart, not both. Auto-start is enabled by default."
}

if ($env:OS -ne "Windows_NT") {
    throw "This installer supports Windows 10/11 x64 only."
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "A 64-bit Windows installation is required."
}
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA is not available. Pass -InstallRoot explicitly."
    }
    $InstallRoot = Join-Path $env:LOCALAPPDATA "ZoteroBilingualLinkedReader"
}
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$SourceRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$BackendSource = Join-Path $SourceRoot "backend"
$WindowsRequirementsSource = Join-Path $PSScriptRoot "requirements-win.txt"
$LoopbackPatchSource = Join-Path $PSScriptRoot "patches\zotero-pdf2zh-v4.0.3-loopback.patch"
$NoticesSource = Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md"
$BundledPayloadDir = Join-Path $PSScriptRoot "payload"
$BundledLicensesDir = Join-Path $PSScriptRoot "licenses"
$BundledServerArchive = Join-Path $BundledPayloadDir "zotero-pdf2zh-server-$Pdf2zhVersion.zip"
$BundledPdf2zhAddon = Join-Path $BundledPayloadDir "zotero-pdf2zh-$Pdf2zhVersion.xpi"
$BundledUvArchive = Join-Path $BundledPayloadDir "uv-$UvVersion-windows-x64.zip"
$BundledPdf2zhLicense = Join-Path $BundledLicensesDir "ZOTERO-PDF2ZH-AGPL-3.0.txt"
$BundledPdf2zhNextLicense = Join-Path $BundledLicensesDir "PDFMATHTRANSLATE-NEXT-AGPL-3.0.txt"
$BundledBabelDocLicense = Join-Path $BundledLicensesDir "BABELDOC-AGPL-3.0.txt"

$addonManifest = Join-Path $SourceRoot "addon\manifest.json"
if (Test-Path -LiteralPath $addonManifest) {
    $manifest = Get-Content -LiteralPath $addonManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    $BundleVersion = [string]$manifest.version
    $ourAddonCandidates = @(
        (Join-Path $SourceRoot "addons\bilingual-linked-reader-$BundleVersion.xpi"),
        (Join-Path $SourceRoot "dist\bilingual-linked-reader-$BundleVersion.xpi")
    )
    $OurAddonSource = $ourAddonCandidates |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}
else {
    $bundledAddonsDir = Join-Path $SourceRoot "addons"
    $bundledAddons = @(
        Get-ChildItem -LiteralPath $bundledAddonsDir -Filter "bilingual-linked-reader-*.xpi" -File -ErrorAction SilentlyContinue
    )
    if ($bundledAddons.Count -ne 1) {
        throw "The Windows bundle must contain exactly one addons\bilingual-linked-reader-*.xpi file."
    }
    $OurAddonSource = $bundledAddons[0].FullName
    $versionMatch = [regex]::Match($bundledAddons[0].Name, '^bilingual-linked-reader-(?<version>.+)\.xpi$')
    if (-not $versionMatch.Success) {
        throw "Could not derive the bundled add-on version from $($bundledAddons[0].Name)."
    }
    $BundleVersion = $versionMatch.Groups['version'].Value
}
if ([string]::IsNullOrWhiteSpace([string]$OurAddonSource)) {
    throw "bilingual-linked-reader-$BundleVersion.xpi is missing from the Windows bundle."
}

if (-not (Test-Path -LiteralPath (Join-Path $BackendSource "watch_translated.py"))) {
    throw "The Windows bundle is incomplete: backend\watch_translated.py is missing. Extract the complete ZIP first."
}
foreach ($requiredBundleFile in @(
    $WindowsRequirementsSource,
    $LoopbackPatchSource,
    $NoticesSource,
    $OurAddonSource,
    $BundledServerArchive,
    $BundledPdf2zhAddon,
    $BundledUvArchive,
    $BundledPdf2zhLicense,
    $BundledPdf2zhNextLicense,
    $BundledBabelDocLicense
)) {
    if (-not (Test-Path -LiteralPath $requiredBundleFile)) {
        throw "The Windows bundle is incomplete: $requiredBundleFile is missing."
    }
}

Write-Host "Zotero Bilingual Linked Reader - Windows setup $BundleVersion" -ForegroundColor Cyan
Write-Host "Install root: $InstallRoot"
Write-Host "Local service: http://127.0.0.1:$Port"
Write-Host ""
Write-Host "This setup verifies bundled PDF2zh/uv payloads, then downloads a private Python runtime and pinned dependencies."
Write-Host "No administrator rights, system Python, or WSL are required."

if ($DryRun) {
    Write-Host ""
    Write-Host "DRY RUN: payload and parameters are valid. No files were changed." -ForegroundColor Green
    Write-Host "Bundled PDF2zh Server: $BundledServerArchive"
    Write-Host "Bundled PDF2zh add-on: $BundledPdf2zhAddon"
    Write-Host "Bundled uv runtime: $BundledUvArchive"
    exit 0
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function New-Directory {
    param([string]$Path)
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Test-ExpectedHash {
    param(
        [string]$Path,
        [string]$Expected
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    return $actual -eq $Expected.ToLowerInvariant()
}

function Invoke-Download {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$ExpectedSha256 = "",
        [string]$BundledSource = ""
    )

    if ($ExpectedSha256 -and (Test-ExpectedHash -Path $Destination -Expected $ExpectedSha256)) {
        Write-Host "Using verified download: $([System.IO.Path]::GetFileName($Destination))"
        return
    }
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Force
    }
    $partial = "$Destination.partial"
    if (Test-Path -LiteralPath $partial) {
        Remove-Item -LiteralPath $partial -Force
    }
    if (-not [string]::IsNullOrWhiteSpace($BundledSource)) {
        if (-not (Test-Path -LiteralPath $BundledSource -PathType Leaf)) {
            throw "Bundled payload is missing: $BundledSource"
        }
        if ($ExpectedSha256 -and -not (Test-ExpectedHash -Path $BundledSource -Expected $ExpectedSha256)) {
            throw "Bundled payload failed SHA-256 verification: $BundledSource"
        }
        Write-Host "Using bundled payload: $([System.IO.Path]::GetFileName($BundledSource))"
        Copy-Item -LiteralPath $BundledSource -Destination $partial -Force
    }
    else {
        Write-Host "Downloading $Url"
        $webRequestError = $null
        try {
            # Windows PowerShell 5.1 can otherwise negotiate an obsolete TLS mode
            # on older Windows installations.
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $partial
        }
        catch {
            $webRequestError = $_
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        }
        if ($null -ne $webRequestError) {
            $curlCommand = Get-Command "curl.exe" -ErrorAction SilentlyContinue
            if ($null -eq $curlCommand) {
                throw "Download failed with Invoke-WebRequest and curl.exe is unavailable: $webRequestError"
            }
            Write-Warning "Invoke-WebRequest failed; retrying with Windows curl.exe."
            & $curlCommand.Source `
                --fail `
                --location `
                --retry 3 `
                --retry-delay 2 `
                --connect-timeout 30 `
                --output $partial `
                -- $Url
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $partial)) {
                $curlExitCode = $LASTEXITCODE
                Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
                throw "Download failed with Invoke-WebRequest ($webRequestError) and curl.exe (exit code $curlExitCode): $Url"
            }
        }
    }
    if ($ExpectedSha256 -and -not (Test-ExpectedHash -Path $partial -Expected $ExpectedSha256)) {
        $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
        Remove-Item -LiteralPath $partial -Force
        throw "SHA-256 mismatch for $Url. Expected $ExpectedSha256, got $actual."
    }
    Move-Item -LiteralPath $partial -Destination $Destination -Force
}

function Invoke-Checked {
    param(
        [string]$Label,
        [scriptblock]$Command
    )
    Write-Host $Label
    # The command runs in a child ScriptBlock scope under Windows PowerShell
    # 5.1. Native programs update the global automatic variable there, while
    # reading an uninitialised local $LASTEXITCODE under StrictMode throws.
    $global:LASTEXITCODE = 0
    & $Command
    $exitCode = $global:LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode."
    }
}

function Set-Pdf2zhLoopbackOnly {
    param([string]$ServerScript)

    $unsafeCall = "self.app.run(host='0.0.0.0', port=port, debug=debug)"
    $safeCall = "self.app.run(host='127.0.0.1', port=port, debug=debug)"
    $source = Get-Content -LiteralPath $ServerScript -Raw -Encoding UTF8
    $unsafeCount = ([regex]::Matches($source, [regex]::Escape($unsafeCall))).Count
    $safeCount = ([regex]::Matches($source, [regex]::Escape($safeCall))).Count

    if ($unsafeCount -eq 0 -and $safeCount -eq 1) {
        Write-Host "Verified PDF2zh Server loopback-only patch."
        return
    }
    if ($unsafeCount -ne 1 -or $safeCount -ne 0) {
        throw "Refusing to patch unexpected PDF2zh Server source. Expected exactly one 0.0.0.0 listener and no existing loopback replacement."
    }

    $patched = $source.Replace($unsafeCall, $safeCall)
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ServerScript, $patched, $utf8WithoutBom)

    $verified = Get-Content -LiteralPath $ServerScript -Raw -Encoding UTF8
    if (-not $verified.Contains($safeCall) -or $verified.Contains($unsafeCall)) {
        throw "PDF2zh Server loopback-only patch verification failed."
    }
    Write-Host "Applied PDF2zh Server loopback-only security patch."
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$TargetPath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [string]$Description
    )
    New-Directory -Path (Split-Path -Parent $Path)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Description = $Description
    $shortcut.Save()
}

$binDir = Join-Path $InstallRoot "bin"
$backendDir = Join-Path $InstallRoot "backend"
$serverDir = Join-Path $InstallRoot "pdf2zh-server"
$runtimeDir = Join-Path $InstallRoot "runtime"
$uvDir = Join-Path $runtimeDir "uv"
$uvExe = Join-Path $uvDir "uv.exe"
$venvDir = Join-Path $runtimeDir "venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$downloadDir = Join-Path $InstallRoot "downloads"
$configDir = Join-Path $InstallRoot "config"
$stateDir = Join-Path $InstallRoot "state"
$logDir = Join-Path $InstallRoot "logs"
$addonsDir = Join-Path $InstallRoot "addons"
$licensesDir = Join-Path $InstallRoot "licenses"
$installedPatchesDir = Join-Path $licensesDir "patches"
$installedRequirements = Join-Path $configDir "requirements-win.txt"
$hadExistingInstallation = Test-Path -LiteralPath (Join-Path $configDir "install.json")

foreach ($directory in @(
    $InstallRoot, $binDir, $backendDir, $runtimeDir, $downloadDir,
    $configDir, $stateDir, $logDir, $addonsDir, $licensesDir, $installedPatchesDir
)) {
    New-Directory -Path $directory
}

Write-Host "Installing Bilingual Linked Reader backend files..."
foreach ($scriptName in @("common.ps1", "start.ps1", "stop.ps1", "status.ps1")) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $scriptName) -Destination (Join-Path $binDir $scriptName) -Force
}
if ($hadExistingInstallation) {
    $setupPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    Invoke-Checked -Label "Stopping the existing local backend before updating it..." -Command {
        & $setupPowerShell -NoLogo -NoProfile -ExecutionPolicy Bypass `
            -File (Join-Path $binDir "stop.ps1") -InstallRoot $InstallRoot
    }
}
Get-ChildItem -LiteralPath $BackendSource -File |
    Where-Object { $_.Extension -eq ".py" -or $_.Name -eq "requirements.txt" } |
    Copy-Item -Destination $backendDir -Force
Copy-Item -LiteralPath $NoticesSource -Destination (Join-Path $licensesDir "THIRD_PARTY_NOTICES.md") -Force
Copy-Item -LiteralPath $LoopbackPatchSource -Destination (Join-Path $installedPatchesDir "zotero-pdf2zh-v4.0.3-loopback.patch") -Force
Copy-Item -LiteralPath $WindowsRequirementsSource -Destination $installedRequirements -Force
Copy-Item -LiteralPath $BundledPdf2zhLicense -Destination (Join-Path $licensesDir "ZOTERO-PDF2ZH-AGPL-3.0.txt") -Force
Copy-Item -LiteralPath $BundledPdf2zhNextLicense -Destination (Join-Path $licensesDir "PDFMATHTRANSLATE-NEXT-AGPL-3.0.txt") -Force
Copy-Item -LiteralPath $BundledBabelDocLicense -Destination (Join-Path $licensesDir "BABELDOC-AGPL-3.0.txt") -Force

$serverArchive = Join-Path $downloadDir "zotero-pdf2zh-server-$Pdf2zhVersion.zip"
Invoke-Download `
    -Url $Pdf2zhServerUrl `
    -Destination $serverArchive `
    -ExpectedSha256 $Pdf2zhServerSha256 `
    -BundledSource $BundledServerArchive
if (-not (Test-Path -LiteralPath (Join-Path $serverDir "server.py"))) {
    Write-Host "Extracting the official PDF2zh Server..."
    $stage = Join-Path $downloadDir ("server-stage-" + [guid]::NewGuid().ToString("N"))
    New-Directory -Path $stage
    try {
        Expand-Archive -LiteralPath $serverArchive -DestinationPath $stage -Force
        $extractedServer = Join-Path $stage "server"
        if (-not (Test-Path -LiteralPath (Join-Path $extractedServer "server.py"))) {
            throw "The official server archive did not contain server\server.py."
        }
        Move-Item -LiteralPath $extractedServer -Destination $serverDir
    }
    finally {
        if (Test-Path -LiteralPath $stage) {
            [System.IO.Directory]::Delete($stage, $true)
        }
    }
}
New-Directory -Path (Join-Path $serverDir "translated")
Set-Pdf2zhLoopbackOnly -ServerScript (Join-Path $serverDir "server.py")

$upstreamAddon = Join-Path $addonsDir "zotero-pdf2zh-$Pdf2zhVersion.xpi"
Invoke-Download `
    -Url $Pdf2zhAddonUrl `
    -Destination $upstreamAddon `
    -ExpectedSha256 $Pdf2zhAddonSha256 `
    -BundledSource $BundledPdf2zhAddon

$ourAddon = Join-Path $addonsDir "bilingual-linked-reader-$BundleVersion.xpi"
Copy-Item -LiteralPath $OurAddonSource -Destination $ourAddon -Force

if (-not (Test-Path -LiteralPath $uvExe)) {
    Write-Host "Installing the private uv runtime $UvVersion..."
    $uvArchive = Join-Path $downloadDir "uv-$UvVersion-windows-x64.zip"
    Invoke-Download `
        -Url $UvArchiveUrl `
        -Destination $uvArchive `
        -ExpectedSha256 $UvArchiveSha256 `
        -BundledSource $BundledUvArchive
    $uvStage = Join-Path $downloadDir ("uv-stage-" + [guid]::NewGuid().ToString("N"))
    New-Directory -Path $uvStage
    try {
        Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvStage -Force
        $extractedUv = Get-ChildItem -LiteralPath $uvStage -Filter "uv.exe" -File -Recurse | Select-Object -First 1
        if (-not $extractedUv) {
            throw "The official uv archive did not contain uv.exe."
        }
        New-Directory -Path $uvDir
        Copy-Item -LiteralPath $extractedUv.FullName -Destination $uvExe -Force
        $extractedUvx = Get-ChildItem -LiteralPath $uvStage -Filter "uvx.exe" -File -Recurse | Select-Object -First 1
        if ($extractedUvx) {
            Copy-Item -LiteralPath $extractedUvx.FullName -Destination (Join-Path $uvDir "uvx.exe") -Force
        }
    }
    finally {
        if (Test-Path -LiteralPath $uvStage) {
            [System.IO.Directory]::Delete($uvStage, $true)
        }
    }
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $runtimeDir "python"
$env:UV_CACHE_DIR = Join-Path $runtimeDir "uv-cache"
$venvConfig = Join-Path $venvDir "pyvenv.cfg"
$managedPythonRoot = [System.IO.Path]::GetFullPath($env:UV_PYTHON_INSTALL_DIR).TrimEnd("\")
$venvUsesManagedPython = $false
if (Test-Path -LiteralPath $venvConfig) {
    $venvConfigText = Get-Content -LiteralPath $venvConfig -Raw -Encoding UTF8
    $homeMatch = [regex]::Match($venvConfigText, '(?m)^home\s*=\s*(?<home>.+?)\s*$')
    if ($homeMatch.Success) {
        try {
            $venvHome = [System.IO.Path]::GetFullPath($homeMatch.Groups['home'].Value.Trim()).TrimEnd("\")
            $venvUsesManagedPython = (
                $venvHome.Equals($managedPythonRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
                $venvHome.StartsWith($managedPythonRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)
            )
        }
        catch {
            $venvUsesManagedPython = $false
        }
    }
}
if ((Test-Path -LiteralPath $pythonExe) -and -not $venvUsesManagedPython) {
    Write-Warning "The existing environment uses a system Python; rebuilding it with a private uv-managed Python."
    $existingConfig = Join-Path $configDir "install.json"
    if (Test-Path -LiteralPath $existingConfig) {
        $existingPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        Invoke-Checked -Label "Stopping the existing local backend before rebuilding Python..." -Command {
            & $existingPowerShell -NoLogo -NoProfile -ExecutionPolicy Bypass `
                -File (Join-Path $binDir "stop.ps1") -InstallRoot $InstallRoot
        }
    }
    [System.IO.Directory]::Delete($venvDir, $true)
    $venvUsesManagedPython = $false
}
if (-not $venvUsesManagedPython -or -not (Test-Path -LiteralPath $pythonExe)) {
    Invoke-Checked -Label "Creating a private Python 3.12 environment..." -Command {
        & $uvExe venv $venvDir --python 3.12 --managed-python --seed
    }
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Invoke-Checked -Label "Installing pinned translation and sentence-mapping dependencies..." -Command {
    & $uvExe pip install `
        --python $pythonExe `
        --requirement $installedRequirements `
        --only-binary=:all: `
        --exclude-newer $DependencyCutoff
}
Invoke-Checked -Label "Checking the private Python environment..." -Command {
    & $pythonExe -m pip check
}

$modelCacheDir = Join-Path $InstallRoot "model-cache"
New-Directory -Path $modelCacheDir
$env:HF_HUB_DISABLE_XET = "1"
$modelWarmup = @'
import math
import sys
from pathlib import Path

from fastembed import TextEmbedding
from huggingface_hub import snapshot_download

cache_dir = Path(sys.argv[1])
model_name = sys.argv[2]
repo_id = sys.argv[3]
revision = sys.argv[4]
cache_dir.mkdir(parents=True, exist_ok=True)

allow_patterns = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "preprocessor_config.json",
    "model_optimized.onnx",
]
snapshot_path = Path(
    snapshot_download(
        repo_id=repo_id,
        revision=revision,
        cache_dir=str(cache_dir),
        allow_patterns=allow_patterns,
    )
)
if snapshot_path.name != revision:
    raise SystemExit(f"unexpected model snapshot: {snapshot_path}")

repo_cache = cache_dir / f"models--{repo_id.replace('/', '--')}"
main_ref = repo_cache / "refs" / "main"
main_ref.parent.mkdir(parents=True, exist_ok=True)
main_ref.write_text(revision, encoding="utf-8")
if main_ref.read_text(encoding="utf-8").strip() != revision:
    raise SystemExit("failed to pin the FastEmbed main cache reference")

model = TextEmbedding(model_name, cache_dir=str(cache_dir))
vectors = list(model.embed(["Windows semantic alignment installation check."]))
if len(vectors) != 1:
    raise SystemExit(f"expected one embedding, received {len(vectors)}")
values = [float(value) for value in vectors[0]]
if not values or not all(math.isfinite(value) for value in values):
    raise SystemExit("embedding is empty or contains a non-finite value")
norm = math.sqrt(sum(value * value for value in values))
if not math.isfinite(norm) or norm <= 0:
    raise SystemExit(f"embedding norm is invalid: {norm}")
print(f"semantic model ready: dimensions={len(values)}, norm={norm:.6f}")
'@
$modelWarmupScript = Join-Path $stateDir "model-warmup.py"
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($modelWarmupScript, $modelWarmup, $utf8WithoutBom)
try {
    Invoke-Checked -Label "Downloading and validating the local semantic alignment model (first setup only)..." -Command {
        & $pythonExe $modelWarmupScript $modelCacheDir $AlignmentModel $AlignmentModelRepo $AlignmentModelRevision
    }
}
finally {
    Remove-Item -LiteralPath $modelWarmupScript -Force -ErrorAction SilentlyContinue
}

$autoStartEnabled = -not $NoAutoStart
$installConfig = [ordered]@{
    schemaVersion = 1
    bundleVersion = $BundleVersion
    installedAt = [DateTimeOffset]::Now.ToString("o")
    installRoot = $InstallRoot
    port = $Port
    serverURL = "http://127.0.0.1:$Port"
    pdf2zhVersion = $Pdf2zhVersion
    pdf2zhNextRequirement = $Pdf2zhNextRequirement
    uvVersion = $UvVersion
    alignmentModel = $AlignmentModel
    alignmentModelRepo = $AlignmentModelRepo
    alignmentModelRevision = $AlignmentModelRevision
    autoStartEnabled = $autoStartEnabled
}
$installConfig | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $configDir "install.json") -Encoding UTF8

$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$startScript = Join-Path $binDir "start.ps1"
$stopScript = Join-Path $binDir "stop.ps1"
$statusScript = Join-Path $binDir "status.ps1"
$quotedRoot = '"' + $InstallRoot + '"'

if (-not $NoShortcuts) {
    Write-Host "Creating shortcuts..."
    $desktop = [Environment]::GetFolderPath("Desktop")
    $programs = [Environment]::GetFolderPath("Programs")
    $menuDir = Join-Path $programs "Zotero Bilingual Linked Reader"
    New-Shortcut `
        -Path (Join-Path $desktop "Zotero 双语阅读器 - 启动.lnk") `
        -TargetPath $powerShellExe `
        -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`" -InstallRoot $quotedRoot -OpenStatus" `
        -WorkingDirectory $InstallRoot `
        -Description "Start PDF2zh Server and bilingual sentence mapping"
    New-Shortcut `
        -Path (Join-Path $desktop "Zotero 双语阅读器 - 状态.lnk") `
        -TargetPath $powerShellExe `
        -Arguments "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$statusScript`" -InstallRoot $quotedRoot" `
        -WorkingDirectory $InstallRoot `
        -Description "Check Zotero Bilingual Linked Reader backend status"
    New-Shortcut `
        -Path (Join-Path $menuDir "启动后端.lnk") `
        -TargetPath $powerShellExe `
        -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`" -InstallRoot $quotedRoot -OpenStatus" `
        -WorkingDirectory $InstallRoot `
        -Description "Start the local backend"
    New-Shortcut `
        -Path (Join-Path $menuDir "停止后端.lnk") `
        -TargetPath $powerShellExe `
        -Arguments "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$stopScript`" -InstallRoot $quotedRoot" `
        -WorkingDirectory $InstallRoot `
        -Description "Stop only the backend processes owned by this installation"
    New-Shortcut `
        -Path (Join-Path $menuDir "打开插件安装文件夹.lnk") `
        -TargetPath (Join-Path $env:SystemRoot "explorer.exe") `
        -Arguments ('"' + $addonsDir + '"') `
        -WorkingDirectory $InstallRoot `
        -Description "Open the two Zotero XPI installers"

}

$startup = [Environment]::GetFolderPath("Startup")
$autoStartShortcut = Join-Path $startup "Zotero Bilingual Linked Reader Backend.lnk"
if ($autoStartEnabled) {
    New-Shortcut `
        -Path $autoStartShortcut `
        -TargetPath $powerShellExe `
        -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`" -InstallRoot $quotedRoot" `
        -WorkingDirectory $InstallRoot `
        -Description "Start the local Zotero bilingual backend after sign-in"
    Write-Host "Auto-start after Windows sign-in is enabled. Use -NoAutoStart to opt out."
}
elseif (Test-Path -LiteralPath $autoStartShortcut) {
    # A fixed shortcut name can also belong to another installation root.
    # Never let -NoAutoStart for an isolated/test install remove that entry.
    $startupShell = New-Object -ComObject WScript.Shell
    $existingShortcut = $startupShell.CreateShortcut($autoStartShortcut)
    $ownedTarget = (
        [string]$existingShortcut.TargetPath
    ).Equals($powerShellExe, [System.StringComparison]::OrdinalIgnoreCase)
    $existingArguments = [string]$existingShortcut.Arguments
    $ownedArguments = (
        $existingArguments.IndexOf($startScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $existingArguments.IndexOf($InstallRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    )
    if ($ownedTarget -and $ownedArguments) {
        Remove-Item -LiteralPath $autoStartShortcut -Force
        Write-Host "Auto-start after Windows sign-in is disabled."
    }
    else {
        Write-Host "A startup shortcut for another installation was preserved."
    }
}

if (-not $NoStart) {
    Write-Host "Starting the local backend..."
    & $startScript -InstallRoot $InstallRoot
}

Write-Host ""
Write-Host "Windows setup completed successfully." -ForegroundColor Green
Write-Host "1. In Zotero, open Tools -> Add-ons."
Write-Host "2. Install these two files once:"
Write-Host "   $upstreamAddon"
Write-Host "   $ourAddon"
Write-Host "3. Restart Zotero, then right-click a PDF -> PDF2zh -> Translate."
Write-Host ""
if ($autoStartEnabled) {
    Write-Host "After a reboot, the local backend starts automatically after sign-in."
}
else {
    if (-not $NoShortcuts) {
        Write-Host "After a reboot, use the desktop/start-menu shortcut to start the local backend."
    }
    else {
        Write-Host "Auto-start and shortcuts are disabled; run $startScript to start the backend."
    }
}

if (-not $NoOpenAddons) {
    Start-Process explorer.exe -ArgumentList ('"' + $addonsDir + '"') | Out-Null
}
