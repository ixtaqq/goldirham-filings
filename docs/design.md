# Design: Goldirham Filings — cited Q&A over SEC filings, served over MCP

Written: 2026-10-08
Status: DRAFT — awaiting owner review
Owner: Aizat Taqqiyudin
Location: `E:\Workspace\Projects\finance\goldirham-filings` (new repository, not yet initialized)

## 1. Summary

A Python service that answers questions about the SEC filings of the companies Goldirham Stack already tracks. Every answer cites the exact filing passages it used, and the service says so when the filings do not support an answer. It is exposed as an MCP server, so Claude Desktop or any MCP client can call it. A golden question set measures retrieval and answer quality, and CI blocks changes that make retrieval worse.

Goal for the first release: a working, evaluated, demoable system in about three weeks, at $0 running cost.

## 2. Why build this

1. **It closes the gaps recent job descriptions keep naming.** The 6 Oct 2026 triage (`C:\Users\shaxii\Documents\Career\career-ops\data\triage-2026-10-06.md`) marked down several strong fits for the same reasons: strong Python (CompAsia, IBroad, Helius), RAG with evaluation and guardrails (Helius), and authored MCP servers plus CI-gated eval suites (Supabase). No existing project covers retrieval or MCP.
2. **It is on Goldirham Stack's own roadmap.** `ai-infra-digest/docs/project-review-2026-10-06.md` lists "claim-level source citations" as a later-still feature (effort L). This project builds the retrieval and citation layer that feature needs, outside the production digest, without putting the digest at risk.
3. **It avoids building a fourth digest.** The existing finance projects are well tested and their reviews recommend reliability work, not new features. This project adds a capability rather than another surface for the same one.

## 3. Goals and non-goals

### Goals

- Ingest the full text of 10-K and 10-Q filings for the tracked companies, split by section.
- Retrieve relevant passages with hybrid search (keyword + vector).
- Answer only from retrieved passages, with a citation for every factual claim; abstain when the evidence is missing.
- Expose read-only MCP tools: search, fetch a passage, compare across filings.
- Measure quality with a hand-checked golden set; gate CI on deterministic retrieval metrics.
- Run entirely on local models and free data. No paid API calls.

### Non-goals (first release)

- Investment advice, price data, or trading signals. This answers "what does the filing say", not "should I buy".
- 8-K exhibits, earnings-call transcripts, XBRL financial-statement facts. (XBRL is a good later addition for exact numbers.)
- A web UI. The demo is Claude Desktop calling the MCP server.
- Multi-user hosting, authentication, or deployment. Local and single-user first.
- Changes to `ai-infra-digest`. Integration is a later, separate decision.

## 4. What exists today (verified 2026-10-08)

Read from `ai-infra-digest/src/collector/sec.ts` and the migration list:

| Fact | Consequence for this design |
| --- | --- |
| The digest keeps at most 3 filings per company, only from the last 60 days | It cannot supply 10-K history or year-over-year comparisons |
| Filing text is truncated to 15,000 characters before use | A 10-K is far longer; Risk Factors and MD&A are mostly cut off |
| HTML is stripped with regular expressions into one flat string | Section headings (Item 1A, Item 7) are lost, so section-aware chunking needs real HTML parsing |
| No migration creates a table for filing text | Nothing to reuse from the database |
| `TRACKED_COMPANIES` holds 35 tickers with CIKs; the request pattern (User-Agent header, ~150 ms between requests) is sound | Reuse the company list and the polite-fetch pattern; write a new ingester |
| The digest's `USER_AGENT` uses a placeholder contact, `ai-infra@example.com` | SEC fair-access rules ask for a real contact. Use a real address here, and fix the digest separately |

Conclusion: reuse the company list and the fetching conventions, not the stored data. Ingestion is new work.

### Coverage caveats in the company list (verify during M1)

- TSM and ASML are foreign private issuers. They file 20-F and 6-K, not 10-K and 10-Q, so they need either 20-F support or exclusion from the first release. Check ARM's filing types the same way.
- ANSS: Synopsys completed its acquisition of Ansys in 2025, so expect no new Ansys filings. Keep its historical filings or drop it.

## 5. Questions it should answer

These examples set the scope and seed the golden set.

| Type | Example |
| --- | --- |
| Lookup | "What did NVIDIA report as data center revenue in its most recent 10-Q?" |
| Risk | "Does Micron's latest 10-K list customer concentration as a risk factor?" |
| Comparison | "How did Microsoft's description of AI infrastructure capital expenditure change between its last two 10-Ks?" |
| Cross-company | "Which tracked semiconductor-equipment companies mention export controls in their Risk Factors?" |
| Unanswerable (must abstain) | "What is Broadcom's 2030 revenue target?" (not in any filing) |

## 6. Architecture

```
EDGAR ──► ingest ──► parse into sections ──► chunk ──► index (Postgres FTS + pgvector)
                                                              │
MCP client ──► MCP server ──► retrieve (hybrid + optional rerank) ──► answer with citations / abstain
                                                              │
                                      golden set ──► eval harness ──► CI gate + dated reports
```

### 6.1 Ingestion

- Source: EDGAR submissions API for the filing index; Archives for the primary document.
- Scope for M1: 10 Tier 1 companies, latest 2 10-Ks and 6 10-Qs each (about 80 documents). Expand to all eligible tickers after the pipeline is proven.
- Store the raw HTML on disk (`data/raw/{cik}/{accession}.htm`, git-ignored) so parsing can be rerun without refetching.
- Idempotent: a filing is keyed by accession number; reruns skip known filings.
- Polite: real contact in `User-Agent`, at most ~6 requests per second, bounded retries with backoff, timeouts on every request.

### 6.2 Parsing and chunking

- Parse HTML with a real parser (selectolax or BeautifulSoup + lxml). Drop tables of contents, page headers and footers.
- Detect `Item` headings (1, 1A, 7, 7A for 10-K; Part I Item 2, Part II Item 1A for 10-Q) and record the section for every chunk.
- Chunk within sections: about 400–800 tokens, small overlap, never across a section boundary.
- Tables: keep them as text rows inside the chunk for now; record `has_table` so retrieval issues can be traced.
- Each chunk records its character offsets in the cleaned section text, so a citation can be shown and checked exactly.

### 6.3 Storage

Local PostgreSQL 16 with the pgvector extension in Docker (the same Postgres major version Lens and the trading system already use).

| Table | Key columns |
| --- | --- |
| `companies` | `cik`, `ticker`, `name`, `filer_type` |
| `filings` | `accession` (PK), `cik`, `form`, `period_end`, `filed_at`, `source_url`, `raw_path`, `parser_version` |
| `sections` | `id`, `accession`, `item`, `title`, `text` |
| `chunks` | `id`, `section_id`, `ordinal`, `start_char`, `end_char`, `text`, `tsv` (full-text vector), `embedding` (vector), `embedding_model` |

Migrations with Alembic. `parser_version` and `embedding_model` make reprocessing explicit instead of silently mixing versions.

### 6.4 Retrieval

1. Filter by ticker, form and period when the query or tool call specifies them.
2. Run keyword search (Postgres full-text, `ts_rank`) and vector search (cosine) in parallel, top 50 each.
3. Merge with reciprocal rank fusion.
4. Optional cross-encoder rerank of the top 20. Keep it only if the eval shows a measurable improvement for its latency cost.
5. Return the top k (default 8) with filing, section and offsets.

Embedding model: choose in M2 by comparing two or three local models on the golden set's retrieval metrics. Run embeddings locally (through Ollama or sentence-transformers on the RTX 3070). Record the chosen model and dimension in config; changing it requires re-embedding.

### 6.5 Answering

- Model: local `qwen3:8b` through Ollama for development and the demo (already installed for the trading system). The interface stays model-agnostic so a hosted model can be compared later, with explicit approval for any paid call.
- The prompt supplies numbered passages and requires every factual sentence to cite one or more of them.
- The output is structured (JSON validated with Pydantic): answer sentences, each with citation IDs, plus an `answerable` flag.
- Post-check in code: every cited ID must be one of the supplied passages; a sentence with no citation is removed or the whole answer is downgraded. Any number in the answer must appear verbatim in a cited passage, otherwise it is flagged.
- Abstention: if retrieval scores are below a threshold tuned on the golden set, or the model returns `answerable: false`, return "The filings in the index don't answer this" together with the closest passages.

### 6.6 MCP server

Official MCP Python SDK. `pip install mcp` now installs the 2.x line; pin the major version and confirm the current server API against the v2 documentation when M4 starts. Transport: stdio for Claude Desktop.

All tools are read-only.

| Tool | Input | Output |
| --- | --- | --- |
| `search_filings` | `query`, optional `tickers`, `forms`, `period_from`, `period_to`, `k` | Ranked passages with ticker, form, period, section, offsets, snippet |
| `get_passage` | `chunk_id` or `accession` + `start_char` / `end_char` | Full passage text plus surrounding context and the EDGAR URL |
| `answer_question` | `question`, optional filters | Cited answer or abstention, with the passages used |
| `compare_filings` | `ticker`, `section` (for example `1A`), two accessions or "latest two" | Paired passages from both filings for the client to compare |
| `list_coverage` | none | Indexed companies, forms and periods, so the client knows what it can ask |

`compare_filings` returns aligned evidence rather than a generated diff; the calling model does the comparison with citations available.

## 7. Evaluation

### 7.1 Golden set

- 60 questions for the first baseline, growing to 100. Mix: about 35% lookup, 25% risk, 15% comparison, 10% cross-company, 15% unanswerable.
- Stored in `evals/golden.jsonl`. Each item: `id`, `question`, `filters`, `answerable`, `gold_spans` (accession, section, start and end offsets), `gold_answer`, `notes`, `checked_by`, `checked_at`.
- Every item is checked by hand against the filing. Drafting questions with a model is allowed; marking them checked is not.
- Gold spans point to raw text offsets, so a parser change that shifts offsets is detected by a validation script rather than silently breaking the set.

### 7.2 Metrics

| Layer | Metric | Computed by |
| --- | --- | --- |
| Retrieval | Recall@5, Recall@10, MRR against gold spans | Code (deterministic) |
| Citations | Citation precision: share of cited passages that overlap a gold span | Code |
| Numbers | Every number in an answer appears in a cited passage | Code |
| Abstention | Accuracy on unanswerable items; false-abstention rate on answerable items | Code |
| Answer correctness | Agreement with `gold_answer` | Local model as judge, plus a human spot-check of 20 items per release |

A local 8B model judging answers is a weak judge. Report its agreement with the human spot-check next to every score, and treat answer-correctness numbers as indicative until that agreement is known.

### 7.3 CI gate

CI has no GPU, so it runs only the deterministic layer:

- A small fixture corpus (about 10 filings) with precomputed chunk embeddings and golden-query embeddings is committed to the repository, tagged with the embedding model name.
- CI starts a pgvector Postgres service container, loads the fixtures, and runs retrieval and citation metrics.
- The build fails if Recall@10 or MRR falls more than 2 points below the committed baseline in `evals/baseline.json`. Updating the baseline is an explicit, reviewed change.

Full evaluation (all layers, full corpus) runs locally with one command and writes a dated report to `benchmarks/eval-YYYY-MM-DD.json`, following the digest's `benchmarks/` convention.

### 7.4 Proposed first-release targets

These are proposals to adjust once a baseline exists, not measured results.

- Recall@10 at least 0.85 on answerable items.
- Citation precision at least 0.90.
- Zero numbers in answers that are missing from cited passages.
- Abstention accuracy at least 0.80 on unanswerable items, with false abstention at most 0.10.

## 8. Safety and cost

- **Untrusted text.** Filing text is data, never instructions. Passages are delimited in the prompt, and the answer post-check limits output to cited, supplied material. Tools are read-only, so a hostile passage cannot cause side effects.
- **Not advice.** `answer_question` output states that it summarizes filings and is not investment advice.
- **Cost.** EDGAR is free; embeddings and answers run locally. Any paid model comparison needs explicit approval with an estimated cost first.
- **SEC fair access.** Real contact in `User-Agent`, rate limiting, caching of raw documents so nothing is fetched twice.

## 9. Stack and conventions

Matches the trading system's Python conventions so the two repositories read the same way.

- Python 3.11+, `uv` for environments and the lock file.
- `ruff`, `mypy --strict`, `pytest`; Hypothesis for parser and chunker invariants (chunks never cross sections, offsets round-trip to the source text).
- PostgreSQL 16 + pgvector in Docker Compose for development; Alembic migrations.
- GitHub Actions: lint, type check, unit tests, retrieval gate.
- Windows-first developer scripts in PowerShell (`scripts/check.ps1`, `scripts/eval.ps1`), mirroring what CI runs.
- Project setup through Agent Homebase's `project-bootstrap`, then `scripts/projects-doctor.ps1`, and a row in `E:\Workspace\Project\README.md`.

## 10. Milestones

| Milestone | Scope | Effort | Done when |
| --- | --- | --- | --- |
| M0 Setup | Repository, `uv` project, Docker Compose with pgvector, CI skeleton, Agent Homebase bootstrap | 1–2 days | `scripts/check.ps1` and CI both pass on an empty test suite |
| M1 Ingest and parse | EDGAR ingester for 10 companies; section parser; chunker with offsets; filer-type check for TSM, ASML, ARM, ANSS | 3–4 days | About 80 filings indexed; every 10-K has Item 1A and Item 7 detected, or the miss is logged; property tests pass |
| M2 Retrieval and golden set | Full-text and vector search, fusion, filters; compare embedding models; write and check 60 golden items | 4–5 days | First retrieval baseline recorded in `evals/baseline.json`; embedding model chosen on measured results |
| M3 Answers | Cited answering, structured output, post-checks, abstention threshold | 3 days | First full local eval report in `benchmarks/`; human spot-check of 20 answers recorded |
| M4 MCP server | Five tools over stdio; Claude Desktop config example | 2 days | All five tools work from Claude Desktop and from the MCP Inspector |
| M5 Ship | CI retrieval gate, README with architecture and results, short demo recording; add to CV and portfolio site | 2 days | CI gate fails on a deliberately degraded retriever and passes on the real one; README shows the measured numbers |

Total: about 15–18 working days.

## 11. Open questions

1. **20-F filers.** Include TSM and ASML through 20-F support in the first release, or exclude them? Default: exclude, list them under "not covered" in `list_coverage`.
2. **Postgres location.** Local Docker (default, $0, isolated) or the existing Supabase project (closer to an eventual digest integration, but shares a production database)? Default: local Docker.
3. **Reranker.** Keep it only if it improves MRR by at least 3 points at acceptable latency. Default: off until measured.
4. **Repository visibility.** Public on GitHub from the start (better for job applications) or private until M5? Default: public once M1 works; no secrets are needed.
5. **Hosted model comparison.** Worth one approved, budgeted run against a hosted model for the README? Default: no, until local results are in.

## 12. Risks

| Risk | Mitigation |
| --- | --- |
| Filing HTML varies by filer and year; section detection fails | Log every filing with missing sections; fixture tests per filer; fall back to whole-document chunks marked `section = unknown` |
| Tables (financial statements) retrieve poorly as text | Accept for the first release, record `has_table`; XBRL facts are the planned later fix |
| A local 8B model is weak at long, multi-passage answers | Keep passages short and few; measure; the model interface allows a later comparison |
| Golden-set writing takes longer than planned | Start it during M1 from real filings; 60 checked items is the bar, not 100 |
| Scope drift into a UI or the digest | Non-goals in section 3; integration is a separate decision after M5 |

## 13. After the first release (not committed)

- XBRL company facts for exact financial numbers, cited by concept and period.
- 20-F support for foreign filers.
- Feed `search_filings` into `ai-infra-digest` as the evidence source for its claim-level citations roadmap item.
- Hybrid-search tuning and a hosted-model comparison, both measured against the same golden set.
