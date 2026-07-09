<#
.SYNOPSIS
    Register the tokemetry collector as a Windows Scheduled Task that runs at
    logon and keeps running.

.DESCRIPTION
    Creates a task that launches the installed tokemetry-collector against a
    config file. Run in a PowerShell session as the user who runs Claude Code
    (the collector reads that user's %USERPROFILE%\.claude).

.PARAMETER CollectorPath
    Full path to the tokemetry-collector executable (e.g. from pipx/uv).

.PARAMETER ConfigPath
    Full path to the collector TOML configuration file.

.EXAMPLE
    ./Register-Collector.ps1 -CollectorPath "$env:USERPROFILE\.local\bin\tokemetry-collector.exe" `
        -ConfigPath "$env:USERPROFILE\.config\tokemetry\collector.toml"
#>
param(
    [Parameter(Mandatory = $true)][string]$CollectorPath,
    [Parameter(Mandatory = $true)][string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
$taskName = 'tokemetry-collector'

$action = New-ScheduledTaskAction -Execute $CollectorPath -Argument "--config `"$ConfigPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -DontStopOnIdleEnd
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force

Write-Host "Registered scheduled task '$taskName'. Start it now with: Start-ScheduledTask -TaskName $taskName"
