# evolution installer for Windows (default dry-run).
# Usage:
#   .\install.ps1                 # show plan
#   .\install.ps1 -Apply          # link into DSH/Claude/Codex + write global rules
#   .\install.ps1 -Apply -Targets dsh,claude
param(
    [switch]$Apply,
    [string[]]$Targets,
    [switch]$NoRules,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$SkillDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $SkillDir "scripts\evolution.py"

$Python = $env:EVOLUTION_PYTHON
if (-not $Python) {
    $Cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($Cmd) { $Python = $Cmd.Source }
}
if (-not $Python) {
    Write-Error "Python 3.11+ not found. Set EVOLUTION_PYTHON or install Python, then retry."
}

$ArgsList = @($Script, "install")
if ($Apply) { $ArgsList += "--apply" }
if ($Targets -and $Targets.Count -gt 0) { $ArgsList += @("--targets", ($Targets -join ",")) }
if ($NoRules) { $ArgsList += "--no-rules" }
if ($Force) { $ArgsList += "--force" }

Write-Host "evolution installer"
Write-Host "  python : $Python"
Write-Host "  skill  : $SkillDir"
Write-Host "  mode   : $(if ($Apply) { 'APPLY' } else { 'DRY-RUN (add -Apply)' })"
& $Python @ArgsList
exit $LASTEXITCODE
