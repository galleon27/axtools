# Hand a Claude plan file to a DeepSeek-backed Claude Code session.
# Usage: ./dev/implement-plan.ps1 [-Plan <path>]
# Without -Plan, uses the newest plan in ~/.claude/plans.
# The DeepSeek key lives in ~/.deepseek/deepseek.env, never in this repo.

param([string]$Plan)

$launcher = Join-Path $HOME ".deepseek\claude-ds.ps1"
if (-not (Test-Path $launcher)) {
    Write-Error "Missing $launcher"
    exit 1
}

if (-not $Plan) {
    $latest = Get-ChildItem (Join-Path $HOME ".claude\plans") -Filter *.md -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) {
        Write-Error "No plans found in ~/.claude/plans"
        exit 1
    }
    $Plan = $latest.FullName
    Write-Host "Plan: $Plan ($($latest.LastWriteTime))"
    $answer = Read-Host "Implement this plan with DeepSeek? [y/N]"
    if ($answer -notmatch '^[yY]') { exit 0 }
}
elseif (-not (Test-Path $Plan)) {
    Write-Error "Plan not found: $Plan"
    exit 1
}

$Plan = (Resolve-Path $Plan).Path
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $launcher "Implement the plan in $Plan. Follow it exactly; see 'Plan handoff' in CLAUDE.md."
}
finally {
    Pop-Location
}
