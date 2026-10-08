# Goldirham Filings

Cited question answering over the SEC 10-K and 10-Q filings of tracked AI-infrastructure
companies, served to MCP clients such as Claude Desktop. Design: `docs/design.md`.
Read `E:\workspace\agent-homebase\PROJECT-WORKFLOW.md` for the shared workflow.

## Stack

- Python 3.11+ (local venv is 3.14), `uv` for the environment and `uv.lock`.
  On this machine uv is installed per user; run it as `python -m uv`.
- PostgreSQL 16 + pgvector in Docker (`compose.yaml`, host port 5442), Alembic migrations.
- Ollama for embeddings (`nomic-embed-text` default) and answers (`qwen3:8b`). No paid APIs.
- MCP Python SDK 2.x (`mcp.server.MCPServer`, tests use `mcp.Client`; `FastMCP` no longer exists).

## Commands

| Task | Command |
| --- | --- |
| install | `python -m uv sync` |
| database | `docker compose up -d --wait`, then `.venv\Scripts\alembic upgrade head` |
| ingest | `.venv\Scripts\filings ingest` (EDGAR fetch; needs `SEC_USER_AGENT` in `.env`) |
| embed | `.venv\Scripts\filings embed [--model M]` |
| all checks | `powershell -File scripts\check.ps1` (what CI runs) |
| single test | `.venv\Scripts\python -m pytest tests\test_chunk.py -q` |
| full eval | `powershell -File scripts\eval.ps1` |
| MCP smoke | `.venv\Scripts\python scripts\mcp_smoke.py` (stdio server, dev DB, Ollama) |

## Layout

- `src/goldirham_filings/` holds the package: `edgar` (polite client), `parse` (HTML to sections),
  `chunk`, `ingest`, `embed`, `retrieve` (all read queries), `answer`, `evaluate`, `fixtures`,
  `server` (MCP), `cli`.
- `evals/golden.jsonl` is the golden set; `evals/fixtures/` is the CI corpus; `evals/baseline.json`
  is the gate baseline. `benchmarks/` holds dated eval reports.
- `data/` (git-ignored) holds raw EDGAR HTML under `data/raw/{cik}/{accession}.htm`.

## Conventions

- Parsing, chunking, fusion, answer checks and metrics are pure functions; DB and HTTP stay at the edges.
- Chunk and gold-span offsets index into section text. `section.text[start:end]` must equal the text.
- Changing parsing output means bumping `PARSER_VERSION`; reingest replaces older filings.
- Changing the embedding model means re-embedding, re-exporting fixtures and rewriting the baseline.
- Golden items are drafted by tools or models but only a human sets `checked_by`/`checked_at`.

## Verification

`scripts\check.ps1` must pass. Retrieval changes also need `filings eval retrieval` on the dev
index, compared with the latest `benchmarks/retrieval-*.json`. Updating `evals/baseline.json`
is a reviewed change, never a way to make the gate pass.

## Gotchas

- Intel files use a cross-reference index instead of Item headings; their filings index as one
  `unknown` section by design.
- TSM, ASML and ARM are 20-F filers and are not ingested.
