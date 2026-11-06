# Changelog

Every change below is measured or explained in `docs/decisions.tsv`, with its evidence file.

## 1.0.0 (unreleased)

Waits for an owner-checked held-out set (`evals/heldout.jsonl`) to report v1.0 numbers.

### Added
- `scripts\setup.ps1`: one command from a fresh clone to a working server. It checks for
  Docker and Ollama, syncs, starts the database, migrates, creates `.env` (asking for
  `SEC_USER_AGENT`), pulls the models, ingests, embeds, loads XBRL facts and runs `doctor`.
  Rerunning it changes nothing that is already set up.
- `filings mcp-config` prints the Claude Desktop config for this install.
- `filings update` indexes filings newer than the index, embeds only new chunks and refreshes
  XBRL facts. Each run is recorded, and `list_coverage` reports the last run and each
  company's newest filing.
- `get_financial_fact`: exact reported XBRL values with unit, period, duration and source
  filing, loaded by `filings facts`.
- `INGEST_TICKERS` setting for the companies `filings ingest` covers.
- `scripts\inspector_check.ps1` calls every tool through the MCP Inspector CLI.
- MIT license.

### Changed
- Intel 10-Qs are split into sections on Intel's own headings (parser v4). Intel 10-Ks still
  index as one document.
- XBRL values are plain JSON numbers. A decimal string's schema pattern broke the Inspector's
  output validation.

## 0.2.0 (2026-11-06, pre-release)

### Added
- `filings doctor`: PASS/FAIL per dependency.
- Questions that name a section ("risk factors", "MD&A") search that section.
- Questions about several filings or companies are answered per part; each part can decline.
- `compare_filings` compares a 10-K with a 10-Q, matching sections across forms.
- Alternate gold spans in the golden schema; a drafted held-out set.

### Changed
- Ollama and database failures reach clients as one-sentence errors, not tracebacks.
- Malformed model output is retried once, then the answer declines.
- Sentences with numbers not in their cited passages are removed.
- Period names and dates in answers are checked against the cited filing.
- Ingest skips and reports filings that fail; it exits non-zero if any did.
- Ten time-relative golden questions name their period explicitly.

## 0.1.0 (2026-10-09)

First version: EDGAR ingest, section parsing, hybrid search, cited answers with abstention,
five MCP tools, a golden set with a dev/test split, and a CI retrieval gate.
