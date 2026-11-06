# One command from a fresh clone to a working MCP server. Safe to rerun: an existing .env is
# never touched, ingest skips filings already indexed, embed only fills missing vectors.
# Needs: Python 3.11+, Docker Desktop (running), Ollama (installed and running).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-SecUserAgent "Name email"]
param([string]$SecUserAgent = '')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Step([string]$name, [scriptblock]$step) {
    Write-Host "== $name"
    & $step
    if ($LASTEXITCODE -ne 0) { throw "$name failed (exit $LASTEXITCODE)" }
}

function Stop-Setup([string]$message) {
    Write-Host "setup stopped: $message" -ForegroundColor Red
    exit 1
}

function Get-EnvValue([string]$name, [string]$default) {
    $line = Get-Content .env | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
    if ($null -eq $line) { return $default }
    return ($line -split '=', 2)[1].Trim()
}

# Prerequisites, checked first so a missing one is named before anything is installed.
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Stop-Setup 'Python 3.11+ is not on PATH. Install it from python.org.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Setup 'Docker is not installed. Install Docker Desktop, start it, and rerun.'
}
# A native command's redirected stderr would stop the script under 'Stop'.
$ErrorActionPreference = 'Continue'
docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
$dockerExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($dockerExit -ne 0) {
    Stop-Setup 'Docker is not running. Start Docker Desktop and rerun.'
}
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path $candidate) { $ollama = $candidate }
}
if (-not $ollama) {
    Stop-Setup 'Ollama is not installed. Install it from ollama.com, start it, and rerun.'
}

$ErrorActionPreference = 'Continue'
python -m uv --version 2>$null | Out-Null
$hasUv = $LASTEXITCODE -eq 0
$ErrorActionPreference = 'Stop'
if (-not $hasUv) {
    Invoke-Step 'install uv' { python -m pip install --user uv }
}
Invoke-Step 'install' { python -m uv sync }

if (Test-Path .env) {
    Write-Host '== .env exists; leaving it unchanged'
} else {
    Write-Host '== creating .env from .env.example'
    if (-not $SecUserAgent) {
        $SecUserAgent = Read-Host 'SEC requires a contact for EDGAR requests. Your name and email'
    }
    if ($SecUserAgent -notmatch '@' -or $SecUserAgent -match 'example\.com') {
        Stop-Setup 'SEC_USER_AGENT must be your real name and email, for example "Jane Doe jane@doe.dev".'
    }
    $content = (Get-Content .env.example) -replace '^SEC_USER_AGENT=.*$', "SEC_USER_AGENT=$SecUserAgent"
    [System.IO.File]::WriteAllLines((Join-Path $root '.env'), $content)
}

Invoke-Step 'database' { docker compose up -d --wait }
Invoke-Step 'migrate' { .\.venv\Scripts\alembic.exe upgrade head }

foreach ($model in @((Get-EnvValue 'ANSWER_MODEL' 'qwen3:8b'), (Get-EnvValue 'EMBEDDING_MODEL' 'nomic-embed-text'))) {
    Invoke-Step "pull $model" { & $ollama pull $model }
}

Invoke-Step 'ingest' { .\.venv\Scripts\filings.exe ingest }
Invoke-Step 'embed' { .\.venv\Scripts\filings.exe embed }
Invoke-Step 'XBRL facts' { .\.venv\Scripts\filings.exe facts }
Invoke-Step 'doctor' { .\.venv\Scripts\filings.exe doctor }

Write-Host ''
Write-Host 'Setup complete. Claude Desktop config for this machine:'
.\.venv\Scripts\filings.exe mcp-config
