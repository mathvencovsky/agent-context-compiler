# Windows PowerShell equivalent of the Makefile (no `make` needed).
#   powershell -ExecutionPolicy Bypass -File scripts/make.ps1 <target> [extra args]
# Targets: setup, up, down, migrate, check, test, bench, dev, m10a-pending, extract-eval.
# Extra arguments go to the underlying command, like ARGS="..." in make:
#   .\scripts\make.ps1 extract-eval --split test
# Keep the commands in sync with the Makefile.
param(
    [Parameter(Position = 0)][string]$Target = "help",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest = @()
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Run([string]$Exe, [string[]]$Arguments) {
    Write-Host "> $Exe $($Arguments -join ' ')"
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "failed (exit $LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

switch ($Target) {
    "setup" { & "$PSScriptRoot\setup.ps1" }
    "up" { Run docker @("compose", "up", "-d", "--wait", "--remove-orphans") }
    "down" { Run docker @("compose", "down") }
    "migrate" { Run uv @("run", "alembic", "upgrade", "head") }
    "check" {
        Run uv @("run", "ruff", "check", ".")
        Run uv @("run", "ruff", "format", "--check", ".")
        Run uv @("run", "mypy", "app")
        Run uv @("run", "pytest", "-m", "not browser")
    }
    "test" { Run uv (@("run", "pytest") + $Rest) }
    "bench" { Run uv (@("run", "python", "-m", "bench.run") + $Rest) }
    "dev" { Run uv @("run", "uvicorn", "app.main:app", "--reload") }
    "m10a-pending" {
        # Zero-cost M10a measurements (corpus and provider cache needed). Every
        # "paid calls" line must read 0; stop with Ctrl+C otherwise. The output
        # also goes to m10a-pending.log.
        $steps = @(
            @("bench.m10_result_limits"),
            @("bench.page_health"),
            @("bench.m10a_baseline", "--real"),
            @("bench.m10a_order_dedup", "--half", "A"),
            @("bench.m10a_section_expand", "--half", "A"),
            @("bench.m10a_version", "--half", "A"),
            @("bench.tokenizer_ratio")
        )
        # Windows PowerShell 5.1 turns any stderr line of a redirected native
        # command into a terminating error under "Stop"; uv writes warnings there.
        $ErrorActionPreference = "Continue"
        foreach ($s in $steps) {
            "===== $($s -join ' ')" | Tee-Object -FilePath m10a-pending.log -Append
            & uv run python -m @s 2>&1 | Tee-Object -FilePath m10a-pending.log -Append
            if ($LASTEXITCODE -ne 0) { throw "failed: $($s -join ' ')" }
        }
    }
    "extract-eval" {
        Run uv @("run", "python", "-m", "bench.extract_eval.fetch")
        Run uv (@("run", "python", "-m", "bench.extract_eval.run") + $Rest)
    }
    default {
        Write-Host "usage: .\scripts\make.ps1 <setup|up|down|migrate|check|test|bench|dev|m10a-pending|extract-eval> [args]"
        if ($Target -ne "help") { exit 1 }
    }
}
