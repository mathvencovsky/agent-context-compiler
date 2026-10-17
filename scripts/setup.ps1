# First run on Windows (no make): the same steps as `make setup`.
#   powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
# Then start the API with: uv run uvicorn app.main:app --reload
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "uv sync failed. If it failed building jstyleson, enable Windows long paths or set a short UV_CACHE_DIR (docs/ONBOARDING.md)."
    exit 1
}
if (-not (Test-Path .env)) { Copy-Item .env.example .env }  # never over an existing .env
docker compose up -d --wait --remove-orphans
if ($LASTEXITCODE -ne 0) { exit 1 }
uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "Ready. Start the API: uv run uvicorn app.main:app --reload"
Write-Host "No OpenAI/Jina keys yet? Set PROVIDERS=fake in .env to try assemble (dev only)."
