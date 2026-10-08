# Full local evaluation on the dev index: retrieval, cited answers, abstention and the local
# judge. Needs Ollama with the configured models. Writes benchmarks/eval-YYYY-MM-DD.json.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
.\.venv\Scripts\filings.exe golden validate
if ($LASTEXITCODE -ne 0) { throw 'golden set does not match the index' }
.\.venv\Scripts\filings.exe eval full
if ($LASTEXITCODE -ne 0) { throw 'eval failed' }
