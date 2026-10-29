# NINA for daily use on your own machine, at zero API cost (Windows PowerShell 5.1+).
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 start [-Project C:\path\to\project]
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 stop
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 status
#   powershell -ExecutionPolicy Bypass -File scripts\nina-local.ps1 verify   (end-to-end check)
#
# start: installs llama.cpp if missing, serves Qwen3-Embedding-0.6B (port 8080)
# and Qwen3-Reranker-0.6B (port 8081) on loopback, checks the reranker really
# ranks, creates the separate database nina_dev (never the benchmark's), starts
# the API on http://localhost:8000, registers the NINA MCP server in Claude Code
# and, with -Project, writes the NINA block in that project's AGENTS.md.
# Nothing here calls a paid API. See docs/USO_DIARIO.md.
param(
    [Parameter(Position = 0)][ValidateSet("start", "stop", "status", "verify")][string]$Action = "status",
    [string]$Project = ""
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
Set-Location $Repo
$State = Join-Path $Repo ".nina-local"
New-Item -ItemType Directory -Force -Path $State | Out-Null
$PidFile = Join-Path $State "pids.txt"

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

function Stop-All {
    if (-not (Test-Path $PidFile)) { Say "nothing to stop"; return }
    foreach ($line in Get-Content $PidFile) {
        $name, $id = $line -split " "
        Stop-Process -Id ([int]$id) -Force -ErrorAction SilentlyContinue
        Say "stopped $name ($id)"
    }
    Remove-Item $PidFile
}

switch ($Action) {
    "stop" { Stop-All }
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
            "(db $($h.db), blobs $($h.blobs))"
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
        $env:PROVIDERS = "real"
        $env:EMBEDDINGS_BASE_URL = $EmbedUrl
        $env:EMBEDDING_MODEL = "Qwen3-Embedding-0.6B-Q8_0@Qwen-GGUF"
        $env:EMBEDDING_DIM = "1024"
        $env:EMBEDDINGS_PAD_TO_COLUMN = "true"
        $env:EMBEDDINGS_QUERY_PREFIX = "Instruct: Given a technical question, retrieve relevant code snippets or technical documentation that best answer the question`nQuery:"
        $env:RERANKER_BASE_URL = $RerankUrl
        $env:RERANKER_MODEL = "Qwen3-Reranker-0.6B-Q8_0@ggml-org-GGUF"
        $env:RERANKER_SCORE_TRANSFORM = $transform
        & uv run alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "migration failed" }
        Start-Bg "api" "uv" @("run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
        Wait-Http "$ApiUrl/health" 120 "NINA API"
        Pop-Location

        if (Has "claude") {
            Say "registering the NINA MCP server in Claude Code (user scope)"
            # Windows PowerShell 5.1 turns a native command's stderr into a terminating
            # error under "Stop"; "remove" writes there when nina is not registered yet.
            cmd /c "claude mcp remove nina -s user >nul 2>&1"
            & claude mcp add nina -s user -e "API_URL=$ApiUrl" -e "API_KEY=nina_local_dev" -- `
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
