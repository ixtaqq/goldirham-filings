# Changelog

Every change below is measured or explained in `docs/decisions.tsv`, with its evidence file.

## 1.1.0 (not yet released)

Run `.venv\Scripts\alembic upgrade head` after updating. Clients sending over-long inputs, or
one accession to `compare_filings`, now get an error; see Changed.

### Added
- `COMPARISON_TOPIC_QUERIES` searches each side of a comparison by its topic. On the dev set,
  comparison Recall@10 rose from 0.417 to 0.833 and overall from 0.742 to 0.802; citation
  precision fell from 0.662 to 0.657. Off by default.
- `DROP_UNSUPPORTED_PERIODS` removes sentences naming a period that matches neither the cited
  filing nor its text. Off by default: it never fired on the dev set.
- `CHECK_FACT_DURATIONS` flags a dollar amount the cited filing reports only for other
  durations, such as a nine-month figure quoted as the quarter's. Off by default: it never
  fired on the dev set.
- `abstention_accuracy_with_dated_declines` also credits answers that say the value isn't
  reported and give only other periods' figures. `abstention_accuracy` is unchanged.
- `scripts/mark_checked.py --alternates` records a review of single alternate spans.
- 20-F support behind `INGEST_FOREIGN_FILERS`, off by default: migration 0005, `F-` item ids
  and the `20-F` form in the tool schemas. Read against the latest filings of all three
  companies; ASML's has no Item headings and indexes as one section. XBRL facts load from
  `us-gaap` and `ifrs-full`.

### Changed
- MCP tool inputs have limits, shown in their schemas: 2000 characters for `query`, `question`
  and `focus`, 32 items per filter list, and 20000 characters per `get_passage` span.
- `compare_filings` refuses one accession, or the same accession twice. It used to compare
  the latest two filings, or a filing with itself.
- The MCP server opens read-only database sessions.
- The server writes one JSON line to stderr per tool call: tool, milliseconds, outcome,
  passage count and abstention reason. Questions and passage text are not logged.

### Fixed
- `filings update` and `filings facts` accept lowercase tickers, as `filings ingest` does.
- An unsupported `EMBEDDING_MODEL` or a missing `SEC_USER_AGENT` exits with a message
  instead of a traceback. `filings doctor` checks that the embedding model is supported.
- A company whose XBRL facts fail to load is reported and skipped. It used to end the run
  before the summary printed.
- A question naming an impossible date, such as June 31, no longer raises an error.
- An EDGAR accession that isn't a single path component is refused.

### Security
- `certifi` 2026.10.23 removes the emSign root whose intermediate CA keys were compromised
  (GHSA-6q48-6c7w-9gw2). `httpx` trusts this bundle for EDGAR requests.
- CI and `scripts\check.ps1` run `pip-audit` on the lockfile.

## 1.0.0 (2026-11-06)

Released with three of five held-out targets missed. Results on 25 owner-checked held-out
questions, run once (`benchmarks/eval-all-2026-11-06-heldout-v1.json`,
`benchmarks/retrieval-all-2026-11-06-heldout-v1.json`):

| Metric | Held-out | Target | Met |
| --- | --- | --- | --- |
| Recall@10 | 0.833 | ≥ 0.85 | no |
| Unanswerable declined | 2/4 | ≥ 0.80 | no |
| Wrongly declines answerable | 0.095 (2/21) | ≤ 0.10 | yes |
| Citation precision | 0.614 | ≥ 0.90 | no |
| Answers with unsupported numbers | 0 | 0 | yes |

The README explains each miss. Plan for the gaps: `docs/v1.1-plan.md`.

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
