# NINA for daily use on your own machine, at zero API cost (Windows PowerShell 5.1+).
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 start [-Project C:\path\to\project]
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 stop
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 status
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 verify   (end-to-end check)
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 autostart      (start at logon)
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 autostart-off  (undo it)
#
# start: installs llama.cpp if missing, serves Qwen3-Embedding-0.6B (port 8080)
# and Qwen3-Reranker-0.6B (port 8081) on loopback, checks the reranker really
# ranks, creates the separate database nina_dev (never the benchmark's), starts
# the API on http://localhost:8000, registers the NINA MCP server in Claude Code
# and, with -Project, writes the NINA block in that project's AGENTS.md.
# Nothing here calls a paid API. See docs/USO_DIARIO.md.
param(
    [Parameter(Position = 0)][ValidateSet("start", "stop", "status", "verify", "autostart", "autostart-off")][string]$Action = "status",
    [string]$Project = "",
    # start at logon (the autostart shortcut): log to a file, do nothing if NINA is
    # up, open Docker Desktop if needed, keep the MCP registration as it is.
    [switch]$Boot
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
Set-Location $Repo
$State = Join-Path $Repo ".nina-local"
New-Item -ItemType Directory -Force -Path $State | Out-Null
$PidFile = Join-Path $State "pids.txt"
$Startup = Join-Path ([Environment]::GetFolderPath("Startup")) "NINA.lnk"

$EmbedRepo = "Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0"
$RerankRepo = "ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF"
$EmbedUrl = "http://127.0.0.1:8080"
$RerankUrl = "http://127.0.0.1:8081"
# 127.0.0.1, not localhost: on Windows "localhost" tries ::1 first, and the
# API listens on IPv4 loopback only.
$ApiUrl = "http://127.0.0.1:8000"

function Say([string]$m) { Write-Host "== $m" -ForegroundColor Cyan }
function Has([string]$exe) { [bool](Get-Command $exe -ErrorAction SilentlyContinue) }

function Wait-Http([string]$url, [int]$seconds, [string]$what) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 $url | Out-Null; return }
        catch { Start-Sleep -Seconds 3 }
    }
    throw "$what did not answer at $url within $seconds s (see the logs in $State)"
}

function Start-Bg([string]$name, [string]$exe, [string[]]$arguments) {
    $log = Join-Path $State "$name.log"
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru
    Add-Content -Path $PidFile -Value "$name $($p.Id)"
    Say "$name started (pid $($p.Id), log $log)"
}

function Wait-Docker {
    cmd /c "docker info >nul 2>&1"
    if ($LASTEXITCODE -eq 0) { return }
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) { Start-Process $desktop }
    $deadline = (Get-Date).AddSeconds(300)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        cmd /c "docker info >nul 2>&1"
        if ($LASTEXITCODE -eq 0) { return }
    }
    throw "Docker did not answer within 300 s"
}

function Stop-All {
    if (-not (Test-Path $PidFile)) { Say "nothing to stop"; return }
    foreach ($line in Get-Content $PidFile) {
        $name, $id = $line -split " "
        # pids.txt survives a reboot, and the id may now belong to another program.
        $alive = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
        if (-not $alive -or $alive.ProcessName -notin @("llama-server", "uv")) { continue }
        # The whole tree: killing uv alone leaves uvicorn's python holding port 8000.
        # cmd does the redirect: PS 5.1 throws on native stderr under 2>$null with Stop.
        & cmd /c "taskkill /T /F /PID $id >nul 2>&1"
        Say "stopped $name ($id)"
    }
    Remove-Item $PidFile
}

switch ($Action) {
    "stop" { Stop-All }
    "autostart" {
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut($Startup)
        $link.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" start -Boot"
        $link.WorkingDirectory = $Repo
        $link.WindowStyle = 7
        $link.Save()
        Say "NINA will start at logon ($Startup). Log: .nina-local\boot.log"
    }
    "autostart-off" {
        Remove-Item $Startup -ErrorAction SilentlyContinue
        Say "NINA will no longer start at logon"
    }
    "verify" {
        # End-to-end check: services up, a real assemble, the MCP registration.
        $ok = $true
        function Check([string]$name, [scriptblock]$test) {
            try { $detail = & $test; Write-Host "PASS $name $detail" -ForegroundColor Green }
            catch { Write-Host "FAIL $name : $($_.Exception.Message)" -ForegroundColor Red; $script:ok = $false }
        }
        Check "embeddings server" { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$EmbedUrl/health" | Out-Null }
        Check "reranker server" { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$RerankUrl/health" | Out-Null }
        Check "NINA API" {
            # /health checks the database and the S3 store; the first call is slow.
            try { $h = Invoke-RestMethod -TimeoutSec 60 "$ApiUrl/health" }
            catch {
                $alive = Get-Content $PidFile -ErrorAction SilentlyContinue | Where-Object { $_ -like "api *" } |
                    ForEach-Object { Get-Process -Id ([int]($_ -split " ")[1]) -ErrorAction SilentlyContinue }
                $state = if ($alive) { "API process running" } else { "API process NOT running" }
                throw "$($_.Exception.Message) ($state)"
            }
            if ($h.status -ne "ok") { throw "db=$($h.db) blobs=$($h.blobs)" }
            "(db $($h.db), blobs $($h.blobs), version $($h.version))"
        }
        Check "assemble (real search, up to 5 min on CPU)" {
            $body = @{ query = "How do I read a file line by line?";
                urls = @("https://docs.python.org/3/tutorial/inputoutput.html"); max_tokens = 1500 } | ConvertTo-Json
            $t = Get-Date
            $r = Invoke-RestMethod -Method Post -Uri "$ApiUrl/v1/context/assemble" -ContentType "application/json" -Body $body -TimeoutSec 300
            $n = @($r.fragments).Count
            if ($n -eq 0) { throw "no fragments; warnings: $($r.warnings | ConvertTo-Json -Compress)" }
            $text = ($r.fragments | ForEach-Object { $_.md }) -join " "
            if ($text -notmatch "readline|for line in") { throw "$n fragments, none about reading lines" }
            "($n fragments, coverage $($r.coverage), $([int]((Get-Date) - $t).TotalSeconds) s)"
        }
        if (Has "claude") {
            Check "MCP server registered in Claude Code" {
                $out = (& claude mcp get nina 2>&1) -join " "
                if ($LASTEXITCODE -ne 0) { throw $out }
            }
        } else { Write-Host "SKIP Claude Code not found (claude)" }
        if ($ok) { Write-Host "ALL PASS: NINA is working." -ForegroundColor Green }
        else {
            Write-Host "Something failed. Send this output and the lines below to Claude." -ForegroundColor Yellow
            foreach ($f in @("api", "embed", "rerank")) {
                $log = Join-Path $State "$f.log.err"
                if (Test-Path $log) { Write-Host "--- $f.log.err"; Get-Content $log -Tail 15 }
            }
        }
    }
    "status" {
        foreach ($u in @("$EmbedUrl/health", "$RerankUrl/health", "$ApiUrl/health")) {
            try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $u | Out-Null; Write-Host "up   $u" }
            catch { Write-Host "down $u" }
        }
    }
    "start" {
        if ($Boot) {
            Start-Transcript -Path (Join-Path $State "boot.log") | Out-Null
            try {
                Invoke-RestMethod -TimeoutSec 10 "$ApiUrl/health" | Out-Null
                Say "NINA is already up"
                exit 0
            } catch { Say "NINA is down: starting it" }
            Wait-Docker
        }
        Stop-All
        foreach ($exe in @("docker", "uv", "git")) {
            if (-not (Has $exe)) { throw "$exe is missing: run scripts\setup.ps1 first" }
        }
        if (-not (Has "llama-server")) {
            Say "installing llama.cpp (MIT) with winget"
            winget install --accept-package-agreements --accept-source-agreements llama.cpp
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
            if (-not (Has "llama-server")) { throw "llama-server not found after install; open a new PowerShell and run again" }
        }

        Say "local models (the first run downloads ~1.3 GB from Hugging Face)"
        Start-Bg "embed" "llama-server" @("-hf", $EmbedRepo, "--embedding", "--pooling", "last",
            "-c", "8192", "-b", "8192", "-ub", "8192", "--host", "127.0.0.1", "--port", "8080")
        Start-Bg "rerank" "llama-server" @("-hf", $RerankRepo, "--reranking",
            "-c", "8192", "-b", "8192", "-ub", "8192", "--host", "127.0.0.1", "--port", "8081")
        Wait-Http "$EmbedUrl/health" 1200 "embeddings server"
        Wait-Http "$RerankUrl/health" 1200 "reranker server"

        Say "checking that the reranker ranks (some GGUF conversions return flat scores)"
        $body = @{ model = "rerank"; query = "How do I read a file in Python?";
            documents = @("Use open() and read() to read a file in Python.", "The Eiffel Tower is in Paris.") } |
            ConvertTo-Json
        $r = Invoke-RestMethod -Method Post -Uri "$RerankUrl/v1/rerank" -ContentType "application/json" -Body $body
        $scores = @{}
        foreach ($x in $r.results) { $scores[[int]$x.index] = [double]$x.relevance_score }
        if (-not ($scores[0] -gt $scores[1])) {
            throw "the reranker does not rank (scores $($scores[0]) vs $($scores[1])); send this message to Claude"
        }
        $transform = "none"
        if ($scores.Values | Where-Object { $_ -lt 0 -or $_ -gt 1 }) { $transform = "sigmoid" }
        Say "reranker ok ($($scores[0]) > $($scores[1]); score transform: $transform)"

        Say "database nina_dev (separate from the benchmark's database)"
        Push-Location $Repo
        & docker compose up -d --wait --remove-orphans
        if ($LASTEXITCODE -ne 0) { throw "docker compose up failed (is Docker Desktop running?)" }
        $exists = & docker compose exec -T postgres psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='nina_dev'"
        if ("$exists".Trim() -ne "1") { & docker compose exec -T postgres createdb -U postgres nina_dev }

        # Environment of the API process (overrides .env; nothing is written to disk).
        $env:POSTGRES_DB = "nina_dev"
        # IPv4 loopback: on Windows a refused ::1 attempt ("localhost") costs ~2 s per
        # connection, which made /health take 7 s.
        $env:POSTGRES_HOST = "127.0.0.1"
        $env:S3_ENDPOINT_URL = "http://127.0.0.1:8333"
        $env:PROVIDERS = "real"
        $env:EMBEDDINGS_BASE_URL = $EmbedUrl
        $env:EMBEDDING_MODEL = "Qwen3-Embedding-0.6B-Q8_0@Qwen-GGUF"
        $env:EMBEDDING_DIM = "1024"
        $env:EMBEDDINGS_PAD_TO_COLUMN = "true"
        $env:EMBEDDINGS_QUERY_PREFIX = "Instruct: Given a technical question, retrieve relevant code snippets or technical documentation that best answer the question`nQuery:"
        $env:RERANKER_BASE_URL = $RerankUrl
        $env:RERANKER_MODEL = "Qwen3-Reranker-0.6B-Q8_0@ggml-org-GGUF"
        $env:RERANKER_SCORE_TRANSFORM = $transform
        # Record local use (no API key) for GET /v1/usage and the usage panel.
        $env:RECORD_LOCAL_USAGE = "true"
        $env:NINA_VERSION = "$(& git -C $Repo describe --always --dirty --abbrev=8)".Trim()
        & uv run alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "migration failed" }
        Start-Bg "api" "uv" @("run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
        Wait-Http "$ApiUrl/health" 120 "NINA API"
        Pop-Location

        if ($Boot) {
            Say "MCP registration kept as it is"
        } elseif (Has "claude") {
            Say "registering the NINA MCP server in Claude Code (user scope)"
            # Windows PowerShell 5.1 turns a native command's stderr into a terminating
            # error under "Stop"; "remove" writes there when nina is not registered yet.
            cmd /c "claude mcp remove nina -s user >nul 2>&1"
            # NINA_SCOPE=web: any public page, not only documentation (DECISIONS 2026-11-03).
            & claude mcp add nina -s user -e "API_URL=$ApiUrl" -e "API_KEY=nina_local_dev" -e "NINA_SCOPE=web" -- `
                uv --directory $Repo run python -m app.mcp_server
        } else {
            Write-Host "Claude Code (claude) not found: add the MCP server by hand (docs/USO_DIARIO.md)."
        }
        if ($Project) {
            Say "NINA block in $Project\AGENTS.md"
            & uv run python -m app.cli init --path $Project
        }
        Say "ready. Open Claude Code; /mcp should list 'nina'. Stop with: scripts\nina-local.ps1 stop"
    }
}
