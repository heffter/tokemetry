<#
.SYNOPSIS
    One-command installer for the tokemetry collector on Windows.

.DESCRIPTION
    Installs uv (if missing) and the collector, scaffolds the config, and
    registers the collector as a Scheduled Task that runs at logon and restarts
    on crash. Run it from a clone of the repository in a PowerShell session as
    the user who runs Claude Code (the collector reads that user's
    %USERPROFILE%\.claude).

    The task is registered and started only when the config is complete (a real
    -Token was supplied). Without a token the script installs the collector and
    writes a placeholder config, then stops: edit the config and re-run to
    finish.

.PARAMETER ServerUrl
    Server base URL, e.g. http://10.10.0.1:8787.

.PARAMETER Token
    API bearer token (the bootstrap token for a first run).

.PARAMETER MachineName
    Machine label in the dashboard. Defaults to this computer's name.

.PARAMETER ConfigPath
    Config file path. Defaults to %USERPROFILE%\.config\tokemetry\collector.toml.

.PARAMETER FromGit
    Install the collector from GitHub instead of from this clone.

.PARAMETER NoService
    Install and configure only; do not register the Scheduled Task.

.EXAMPLE
    ./install.ps1 -ServerUrl "http://10.10.0.1:8787" -Token "tkm_xxx" -MachineName "desk-windows"

.EXAMPLE
    ./install.ps1   # installs and scaffolds a placeholder config to edit, then re-run
#>
[CmdletBinding()]
param(
    [string]$ServerUrl,
    [string]$Token,
    [string]$MachineName,
    [string]$ConfigPath = (Join-Path $env:USERPROFILE '.config\tokemetry\collector.toml'),
    [switch]$FromGit,
    [switch]$NoService
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$GitSpec = 'tokemetry-collector @ git+https://github.com/heffter/tokemetry.git#subdirectory=apps/collector'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ExampleConfig = Join-Path $RepoRoot 'deploy\collector.example.toml'
$RegisterScript = Join-Path $PSScriptRoot 'windows\Register-Collector.ps1'

function Write-Step { param([string]$Message) Write-Host "`n==> $Message" }
function Write-Detail { param([string]$Message) Write-Host "  $Message" }

function Install-Uv {
    Write-Step 'Ensuring uv is installed'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Detail "uv already present: $((Get-Command uv).Source)"
        return
    }
    Write-Detail 'installing uv from https://astral.sh/uv/install.ps1'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # The installer drops uv in %USERPROFILE%\.local\bin; make it visible now.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv installation did not put 'uv' on PATH; open a new shell and re-run"
    }
}

function Install-Collector {
    Write-Step 'Installing the collector'
    if ($FromGit) {
        Write-Detail "source: GitHub ($GitSpec)"
        $source = $GitSpec
    }
    else {
        $source = Join-Path $RepoRoot 'apps\collector'
        if (-not (Test-Path (Join-Path $source 'pyproject.toml'))) {
            throw "collector package not found at $source; run from a clone or pass -FromGit"
        }
        Write-Detail "source: $source"
    }
    # --force makes re-runs upgrade in place instead of erroring.
    & uv tool install --force $source
    if ($LASTEXITCODE -ne 0) { throw "uv tool install failed (exit $LASTEXITCODE)" }
}

function Get-CollectorBin {
    $cmd = Get-Command tokemetry-collector -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return (Join-Path $env:USERPROFILE '.local\bin\tokemetry-collector.exe')
}

function Set-ConfigLine {
    param([string]$Content, [string]$Key, [string]$Value)
    # Replace via a MatchEvaluator so '$' in the value is treated literally.
    $pattern = "(?m)^$Key = .*"
    return [regex]::Replace($Content, $pattern, { "$Key = `"$Value`"" }.GetNewClosure())
}

function New-Config {
    Write-Step "Scaffolding config at $ConfigPath"
    $dir = Split-Path -Parent $ConfigPath
    New-Item -ItemType Directory -Force $dir | Out-Null
    if (Test-Path $ConfigPath) {
        Write-Detail 'config already exists; leaving it untouched'
        if ($ServerUrl -or $Token -or $MachineName) {
            Write-Warning "config exists, so -ServerUrl/-Token/-MachineName were NOT applied; edit $ConfigPath by hand"
        }
        return
    }
    if (-not (Test-Path $ExampleConfig)) {
        throw "example config not found at $ExampleConfig; run from a clone"
    }
    $content = Get-Content -Raw $ExampleConfig
    if ($ServerUrl)   { $content = Set-ConfigLine $content 'server_url' $ServerUrl }
    if ($Token)       { $content = Set-ConfigLine $content 'api_token' $Token }
    if ($MachineName) { $content = Set-ConfigLine $content 'machine_name' $MachineName }
    # Write UTF-8 WITHOUT a BOM. Windows PowerShell 5.1's `Set-Content -Encoding
    # utf8` prepends a BOM, which Python's tomllib rejects; the .NET writer with
    # UTF8Encoding($false) is BOM-free on both 5.1 and 7+.
    [System.IO.File]::WriteAllText($ConfigPath, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Detail "wrote $ConfigPath"
}

function Test-ConfigReady {
    # Ready once the placeholder token has been replaced with a real one.
    if (-not (Test-Path $ConfigPath)) { return $false }
    return -not (Select-String -Path $ConfigPath -Pattern 'tkm_replace_me|tkm_change_me' -Quiet)
}

function Invoke-SmokeTest {
    param([string]$Bin)
    Write-Step 'Verifying (dry run, uploads nothing)'
    & $Bin --config $ConfigPath --dry-run
    if ($LASTEXITCODE -eq 0) {
        Write-Detail 'dry run OK'
    }
    else {
        Write-Warning "dry run failed; check server_url/api_token in $ConfigPath"
    }
}

function Register-Service {
    param([string]$Bin)
    Write-Step 'Registering the Scheduled Task'
    & $RegisterScript -CollectorPath $Bin -ConfigPath $ConfigPath
    Start-ScheduledTask -TaskName 'tokemetry-collector'
    Write-Detail 'started; check state with: Get-ScheduledTask -TaskName tokemetry-collector | Get-ScheduledTaskInfo'
}

# --- main ---
# Give every machine a real label even when -MachineName is omitted.
if (-not $MachineName) { $MachineName = $env:COMPUTERNAME }

Install-Uv
Install-Collector
$bin = Get-CollectorBin
Write-Detail "collector binary: $bin"
New-Config

if (-not (Test-ConfigReady)) {
    Write-Step 'Config needs values before the collector can run'
    Write-Detail "edit ${ConfigPath}: set server_url, api_token, machine_name"
    Write-Detail "then finish with: $PSCommandPath -ConfigPath `"$ConfigPath`""
    Write-Detail '(re-running is safe; it will not overwrite your edited config)'
    return
}

Invoke-SmokeTest -Bin $bin

if ($NoService) {
    Write-Step 'Skipping service registration (-NoService)'
    return
}
Register-Service -Bin $bin
Write-Step 'Done. The collector is installed and running.'
