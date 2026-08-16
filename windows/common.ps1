Set-StrictMode -Version 2.0

function Get-BlrLayout {
    param([string]$InstallRoot = "")

    if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
        $InstallRoot = Split-Path -Parent $PSScriptRoot
    }
    $root = [System.IO.Path]::GetFullPath($InstallRoot)
    return [pscustomobject]@{
        Root = $root
        BinDir = Join-Path $root "bin"
        BackendDir = Join-Path $root "backend"
        ServerDir = Join-Path $root "pdf2zh-server"
        TranslatedDir = Join-Path $root "pdf2zh-server\translated"
        RuntimeDir = Join-Path $root "runtime"
        VenvDir = Join-Path $root "runtime\venv"
        PythonExe = Join-Path $root "runtime\venv\Scripts\python.exe"
        UvExe = Join-Path $root "runtime\uv\uv.exe"
        ModelCacheDir = Join-Path $root "model-cache"
        StateDir = Join-Path $root "state"
        MappingStatus = Join-Path $root "pdf2zh-server\translated\.bilingual-mapping-status.json"
        ServerPid = Join-Path $root "state\server.pid"
        WatcherPid = Join-Path $root "state\watcher.pid"
        ConfigPath = Join-Path $root "config\install.json"
        LogDir = Join-Path $root "logs"
        AddonsDir = Join-Path $root "addons"
    }
}

function Read-BlrConfig {
    param($Layout)

    if (-not (Test-Path -LiteralPath $Layout.ConfigPath)) {
        throw "Installation config not found: $($Layout.ConfigPath). Run Install-Windows.cmd first."
    }
    return Get-Content -LiteralPath $Layout.ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function ConvertTo-BlrArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Test-BlrHealth {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 3
    )

    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$Port/health" `
            -TimeoutSec $TimeoutSeconds
        if ([int]$response.StatusCode -ne 200) {
            return $null
        }
        $payload = $response.Content | ConvertFrom-Json
        if ($payload.status -ne "ok") {
            return $null
        }
        return $payload
    }
    catch {
        return $null
    }
}

function Get-BlrListener {
    param([int]$Port)

    try {
        $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
        if ($listeners.Count -eq 0) {
            return $null
        }
        return $listeners
    }
    catch {
        return $null
    }
}

function Get-BlrProcessFromPidFile {
    param(
        [string]$PidFile,
        [string]$InstallRoot,
        [string]$ExpectedExecutable = "",
        [string]$ExpectedCommandFragment = ""
    )

    if (-not (Test-Path -LiteralPath $PidFile)) {
        return $null
    }
    $raw = ([string](Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue)).Trim()
    $processId = 0
    if (-not [int]::TryParse($raw, [ref]$processId)) {
        return $null
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }
    if ([string]::IsNullOrWhiteSpace([string]$process.CommandLine)) {
        return $null
    }
    if ($process.CommandLine.IndexOf($InstallRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return $null
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedExecutable)) {
        $actualExecutable = [string]$process.ExecutablePath
        if ([string]::IsNullOrWhiteSpace($actualExecutable)) {
            return $null
        }
        $expectedFullPath = [System.IO.Path]::GetFullPath($ExpectedExecutable)
        $actualFullPath = [System.IO.Path]::GetFullPath($actualExecutable)
        if (-not $actualFullPath.Equals($expectedFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
    }
    if (
        -not [string]::IsNullOrWhiteSpace($ExpectedCommandFragment) -and
        $process.CommandLine.IndexOf($ExpectedCommandFragment, [System.StringComparison]::OrdinalIgnoreCase) -lt 0
    ) {
        return $null
    }
    return $process
}

function Stop-BlrOwnedProcess {
    param(
        [string]$PidFile,
        [string]$InstallRoot,
        [string]$Label,
        [string]$ExpectedExecutable,
        [string]$ExpectedCommandFragment
    )

    $process = Get-BlrProcessFromPidFile `
        -PidFile $PidFile `
        -InstallRoot $InstallRoot `
        -ExpectedExecutable $ExpectedExecutable `
        -ExpectedCommandFragment $ExpectedCommandFragment
    if ($process) {
        Write-Host "Stopping $Label (PID $($process.ProcessId))..."
        $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
        & $taskkill /PID ([string]$process.ProcessId) /T /F | Out-Null
        $taskkillExitCode = $LASTEXITCODE
        try {
            Wait-Process -Id $process.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
        }
        catch {
            # The process may already have exited.
        }
        if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
            throw "Failed to stop the owned $Label process tree (PID $($process.ProcessId)); taskkill exit code $taskkillExitCode."
        }
    }
    if (Test-Path -LiteralPath $PidFile) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
}

function New-BlrDirectory {
    param([string]$Path)
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Get-BlrOptionalProperty {
    param(
        [object]$Object,
        [string]$Name,
        [object]$Default = $null
    )

    if ($null -eq $Object) {
        return $Default
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $Default
    }
    return $property.Value
}

function Get-BlrMappingStatus {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Test-BlrMappingStatusFresh {
    param(
        [string]$Path,
        [DateTimeOffset]$NotBefore = [DateTimeOffset]::MinValue,
        [int]$MaxAgeSeconds = 15
    )

    $status = Get-BlrMappingStatus -Path $Path
    if ($null -eq $status) {
        return $false
    }
    $updatedAtValue = Get-BlrOptionalProperty -Object $status -Name "updatedAt" -Default ""
    $updatedAt = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$updatedAtValue, [ref]$updatedAt)) {
        return $false
    }
    $now = [DateTimeOffset]::UtcNow
    if ($NotBefore -ne [DateTimeOffset]::MinValue) {
        if ($updatedAt -lt $NotBefore.AddSeconds(-1)) {
            return $false
        }
    }
    if ($updatedAt -gt $now.AddSeconds(5)) {
        return $false
    }
    return ($now - $updatedAt).TotalSeconds -le [Math]::Max(1, $MaxAgeSeconds)
}

function Test-BlrLoopbackListener {
    param($Listener)

    $listeners = @($Listener)
    if ($listeners.Count -eq 0) {
        return $false
    }
    foreach ($item in $listeners) {
        $address = [string](Get-BlrOptionalProperty -Object $item -Name "LocalAddress" -Default "")
        if ($address -notin @("127.0.0.1", "::1")) {
            return $false
        }
    }
    return $true
}

function Test-BlrListenerOwnedByProcessTree {
    param(
        $Listener,
        [int]$RootProcessId
    )

    $listeners = @($Listener)
    if ($listeners.Count -eq 0) {
        return $false
    }
    try {
        $parentByProcess = @{}
        foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
            $parentByProcess[[int]$process.ProcessId] = [int]$process.ParentProcessId
        }
    }
    catch {
        return $false
    }
    foreach ($item in $listeners) {
        $owner = [int](Get-BlrOptionalProperty -Object $item -Name "OwningProcess" -Default 0)
        $current = $owner
        $seen = @{}
        $belongsToTree = $false
        while ($current -gt 0 -and -not $seen.ContainsKey($current)) {
            if ($current -eq $RootProcessId) {
                $belongsToTree = $true
                break
            }
            $seen[$current] = $true
            if (-not $parentByProcess.ContainsKey($current)) {
                break
            }
            $current = [int]$parentByProcess[$current]
        }
        if (-not $belongsToTree) {
            return $false
        }
    }
    return $true
}

function Test-BlrLoopbackServerScript {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $source = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $unsafeCall = "self.app.run(host='0.0.0.0', port=port, debug=debug)"
    $safeCall = "self.app.run(host='127.0.0.1', port=port, debug=debug)"
    return $source.Contains($safeCall) -and -not $source.Contains($unsafeCall)
}
