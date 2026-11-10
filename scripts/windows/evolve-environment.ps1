#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('audit', 'apply')]
    [string]$Mode = 'audit',
    [string]$Workspace = (Get-Location).Path,
    [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }),
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\..\references\windows-tooling.json'),
    [switch]$InstallMissingTools,
    [switch]$UpdateWingetApps,
    [switch]$UpdateCodex,
    [switch]$QuarantineStale
)

$ErrorActionPreference = 'Stop'
$started = Get-Date
$config = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$reportRoot = Join-Path $Workspace '.evolution\reports'
$quarantineRoot = Join-Path $Workspace '.evolution\quarantine'
$null = New-Item -ItemType Directory -Force -Path $reportRoot, $quarantineRoot
$events = [System.Collections.Generic.List[object]]::new()
$checks = [System.Collections.Generic.List[object]]::new()
$changes = [System.Collections.Generic.List[object]]::new()
$failures = [System.Collections.Generic.List[object]]::new()

function Add-Event {
    param([string]$Kind, [string]$Name, [object]$Value)
    $events.Add([pscustomobject]@{ kind = $Kind; name = $Name; value = $Value })
}

function Invoke-Checked {
    param([string]$Name, [string]$FilePath, [string[]]$ArgumentList, [switch]$Change)
    try {
        $output = & $FilePath @ArgumentList 2>&1 | Out-String
        $exitCode = $LASTEXITCODE
        $item = [pscustomobject]@{ name = $Name; command = "$FilePath $($ArgumentList -join ' ')"; exitCode = $exitCode; output = $output.Trim() }
        if ($exitCode -ne 0) { $failures.Add($item) } elseif ($Change) { $changes.Add($item) } else { $checks.Add($item) }
        return $item
    } catch {
        $item = [pscustomobject]@{ name = $Name; command = "$FilePath $($ArgumentList -join ' ')"; exitCode = -1; output = $_.Exception.Message }
        $failures.Add($item)
        return $item
    }
}

function Test-CommandAvailable { param([string]$Name) return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue) }

Add-Event 'meta' 'mode' $Mode
Add-Event 'meta' 'workspace' $Workspace
Add-Event 'meta' 'codexHome' $CodexHome
Add-Event 'meta' 'startedAt' $started.ToString('o')

foreach ($command in @('codex', 'winget', 'git', 'node', 'python', 'npm')) {
    $cmd = Get-Command $command -ErrorAction SilentlyContinue
    Add-Event 'command' $command ([pscustomobject]@{ available = $null -ne $cmd; source = if ($cmd) { $cmd.Source } else { $null } })
}

foreach ($inventory in @(
    @{ Name = 'skills'; Path = Join-Path $CodexHome 'skills' },
    @{ Name = 'plugin-cache'; Path = Join-Path $CodexHome 'plugins\cache' }
)) {
    if (Test-Path $inventory.Path) {
        $entries = Get-ChildItem -LiteralPath $inventory.Path -Directory -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 500 FullName, LastWriteTime
        Add-Event 'inventory' $inventory.Name @($entries)
    } else {
        Add-Event 'inventory' $inventory.Name @()
    }
}

if (Test-CommandAvailable 'codex') {
    $version = Invoke-Checked 'codex-version' 'codex' @('--version')
    Add-Event 'version' 'codex' $version.output
    if ($Mode -eq 'apply') {
        Invoke-Checked 'codex-plugin-marketplace-upgrade' 'codex' @('plugin', 'marketplace', 'upgrade') -Change | Out-Null
        if ($UpdateCodex) { Invoke-Checked 'codex-update' 'codex' @('update') -Change | Out-Null }
    }
    $plugins = Invoke-Checked 'codex-plugin-list' 'codex' @('plugin', 'list', '--json')
    Add-Event 'plugins' 'codex' $plugins.output
}

if (Test-CommandAvailable 'winget') {
    $wingetList = Invoke-Checked 'winget-outdated' 'winget' @('upgrade', '--include-unknown', '--disable-interactivity')
    Add-Event 'packages' 'winget' $wingetList.output
    if ($Mode -eq 'apply' -and $UpdateWingetApps) {
        Invoke-Checked 'winget-upgrade-all' 'winget' @('upgrade', '--all', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity') -Change | Out-Null
    }
}

foreach ($rule in $config.requiredToolRules) {
    $markerPath = Join-Path $Workspace $rule.marker
    if (Test-Path $markerPath) {
        $available = Test-CommandAvailable $rule.command
        Add-Event 'required-tool' $rule.command ([pscustomobject]@{ marker = $rule.marker; required = $true; available = $available; wingetId = $rule.wingetId })
        if (-not $available -and $Mode -eq 'apply' -and $InstallMissingTools -and (Test-CommandAvailable 'winget')) {
            Invoke-Checked "install-$($rule.wingetId)" 'winget' @('install', '--id', $rule.wingetId, '--exact', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity') -Change | Out-Null
        }
    }
}

if ($QuarantineStale) {
    $cutoff = (Get-Date).AddDays(-[int]$config.retentionDays)
    Get-ChildItem -LiteralPath $reportRoot -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        ForEach-Object {
            $target = Join-Path $quarantineRoot (Join-Path (Get-Date -Format 'yyyyMMdd-HHmmss') $_.Name)
            $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target)
            Move-Item -LiteralPath $_.FullName -Destination $target -Force
            $changes.Add([pscustomobject]@{ name = 'quarantine'; source = $_.FullName; destination = $target })
        }
}

$finished = Get-Date
$report = [ordered]@{
    schemaVersion = 1
    startedAt = $started.ToString('o')
    finishedAt = $finished.ToString('o')
    mode = $Mode
    events = @($events)
    checks = @($checks)
    changes = @($changes)
    failures = @($failures)
    status = if ($failures.Count -gt 0) { 'completed-with-failures' } else { 'ok' }
}
$reportPath = Join-Path $reportRoot ("evolution-{0}.json" -f $finished.ToString('yyyyMMdd-HHmmss'))
$report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $reportPath
$summary = "Evolution $($report.status): mode=$Mode changes=$($changes.Count) failures=$($failures.Count) report=$reportPath"
$summaryPath = Join-Path $reportRoot 'latest.txt'
$summary | Set-Content -Encoding UTF8 $summaryPath
Write-Output $summary
if ($failures.Count -gt 0) { exit 2 }
