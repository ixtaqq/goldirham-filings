# Calls every tool once through the MCP Inspector CLI against the real stdio server, as an
# independent client, and writes the responses to benchmarks/inspector-<date>.json.
# Needs Node (npx downloads the pinned Inspector), the dev database and Ollama.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$server = Join-Path $root '.venv\Scripts\goldirham-filings-mcp.exe'
$inspector = '@modelcontextprotocol/inspector@2.14.0'

$calls = @(
    @{ name = 'tools/list'; args = @('--method', 'tools/list') },
    @{ name = 'list_coverage'; args = @('--method', 'tools/call', '--tool-name', 'list_coverage') },
    @{ name = 'search_filings'; args = @('--method', 'tools/call', '--tool-name', 'search_filings',
        '--tool-arg', 'query=export controls China', '--tool-arg', 'tickers=[\"AMAT\"]', '--tool-arg', 'k=2') },
    @{ name = 'get_passage'; args = @('--method', 'tools/call', '--tool-name', 'get_passage',
        '--tool-arg', 'accession=0001045810-26-000075', '--tool-arg', 'section=I-2',
        '--tool-arg', 'start_char=17134', '--tool-arg', 'end_char=17279') },
    @{ name = 'compare_filings'; args = @('--method', 'tools/call', '--tool-name', 'compare_filings',
        '--tool-arg', 'ticker=MSFT', '--tool-arg', 'section=1A', '--tool-arg', 'k=2') },
    @{ name = 'get_financial_fact'; args = @('--method', 'tools/call', '--tool-name', 'get_financial_fact',
        '--tool-arg', 'ticker=AMD', '--tool-arg', 'concept=revenue', '--tool-arg', 'fiscal_year=2026',
        '--tool-arg', 'fiscal_period=Q3') },
    @{ name = 'answer_question'; args = @('--method', 'tools/call', '--tool-name', 'answer_question',
        '--tool-arg', "question=What is Broadcom's 2030 revenue target?", '--tool-arg', 'tickers=[\"AVGO\"]') }
)

$results = [ordered]@{ inspector = $inspector; server = $server; date = (Get-Date -Format s); calls = [ordered]@{} }
$failed = 0
foreach ($call in $calls) {
    # Windows PowerShell drops inner double quotes from native arguments, hence [\"AMAT\"]
    # above; and under 'Stop' the Inspector's stderr would end the script.
    $ErrorActionPreference = 'Continue'
    $output = & npx -y $inspector --cli $server @($call.args) 2>$null | Out-String
    $exit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    $ok = $exit -eq 0 -and $output -notmatch '"isError":\s*true'
    if (-not $ok) { $failed++ }
    Write-Host ("{0,-20} {1}" -f $call.name, $(if ($ok) { 'ok' } else { 'FAILED' }))
    $results.calls[$call.name] = @{ ok = $ok; response = ($output | ConvertFrom-Json) }
}
$out = Join-Path $root ("benchmarks\inspector-{0}.json" -f (Get-Date -Format yyyy-MM-dd))
[System.IO.File]::WriteAllText($out, ($results | ConvertTo-Json -Depth 40))
Write-Host "wrote $out; $failed failed"
exit $failed
