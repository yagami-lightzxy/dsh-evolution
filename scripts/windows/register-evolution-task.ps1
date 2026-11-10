#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$TaskName = 'Evolution Windows Tooling Weekly',
    [string]$Workspace = (Get-Location).Path,
    [ValidatePattern('^\d{2}:\d{2}$')]
    [string]$At = '09:00'
)

$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'evolve-environment.ps1'
$pwsh = (Get-Command pwsh -ErrorAction Stop).Source
$arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$runner`" -Mode apply -InstallMissingTools -UpdateWingetApps -UpdateCodex -QuarantineStale -Workspace `"$Workspace`""
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments -WorkingDirectory $Workspace
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At ([datetime]::ParseExact($At, 'HH:mm', $null))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType InteractiveToken -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Output "Registered '$TaskName' for Mondays at $At (local Windows time) as $identity."
