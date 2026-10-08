# Runs what CI runs: lint, type check, tests, and the retrieval gate on the fixture corpus.
# Uses a scratch database, filings_test, in the dev Postgres container (docker compose up -d).
# The scratch database is dropped and recreated on every run; the dev database is untouched.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Step([string]$name, [scriptblock]$step) {
    Write-Host "== $name"
    & $step
    if ($LASTEXITCODE -ne 0) { throw "$name failed (exit $LASTEXITCODE)" }
}

Invoke-Step 'sync' { python -m uv sync --frozen }
Invoke-Step 'ruff check' { .\.venv\Scripts\ruff.exe check . }
Invoke-Step 'ruff format' { .\.venv\Scripts\ruff.exe format --check . }
Invoke-Step 'mypy' { .\.venv\Scripts\mypy.exe src tests scripts }

Invoke-Step 'scratch database' {
    docker compose exec -T db psql -U filings -d filings -q -c 'DROP DATABASE IF EXISTS filings_test' -c 'CREATE DATABASE filings_test'
}
$env:DATABASE_URL = 'postgresql://filings:filings@127.0.0.1:5442/filings_test'
$env:TEST_DATABASE_URL = $env:DATABASE_URL
try {
    Invoke-Step 'migrate' { .\.venv\Scripts\alembic.exe upgrade head }
    Invoke-Step 'load fixtures' { .\.venv\Scripts\filings.exe fixtures load }
    Invoke-Step 'pytest' { .\.venv\Scripts\python.exe -m pytest -q }
    Invoke-Step 'retrieval gate' { .\.venv\Scripts\filings.exe eval gate }
}
finally {
    Remove-Item Env:DATABASE_URL, Env:TEST_DATABASE_URL
}
Write-Host 'All checks passed.'
