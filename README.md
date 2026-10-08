# Goldirham Filings

Ask questions about the SEC 10-K and 10-Q filings of AI-infrastructure companies and get
answers that cite the exact filing passages, or an explicit "the filings don't answer this".
It runs as an MCP server, so Claude Desktop or any MCP client can call it. Everything runs
locally: EDGAR is free, and embeddings and answers come from local models through Ollama.

It summarizes what filings say. It is not investment advice.

## How it works

```
EDGAR ─► ingest ─► sections (Item 1A, 7, ...) ─► chunks ─► Postgres full-text + pgvector
MCP client ─► MCP server ─► hybrid search (RRF) ─► cited answer or abstention
golden set ─► eval harness ─► CI retrieval gate + dated reports in benchmarks/
```

- **Ingest.** A polite EDGAR client (real contact in `User-Agent`, about 6 requests per
  second, retries with backoff) fetches each filing once into `data/raw/`. Reruns skip
  filings already indexed with the current parser version.
- **Parse.** Inline-XBRL HTML becomes block-aware text with table cells joined by ` | `.
  Item headings split it into sections. Each item keeps the heading that owns the most text
  after the body starts, which skips the table of contents. A filing whose required sections
  (10-K Items 1A and 7, 10-Q Part I Item 2) are missing or stubs falls back to one whole-document
  section, so no text is lost.
- **Chunk.** About 500 tokens per chunk, never across a section boundary, with a one-paragraph
  overlap. Every chunk stores its offsets, and `section.text[start:end] == chunk.text`
  (Hypothesis property tests).
- **Scope.** Each company has eight similar filings, and passages repeat from quarter to
  quarter. Wording like "most recent 10-Q", "fiscal 2025 10-K", "last two 10-Qs" or
  "quarter ended June 27, 2026" is resolved to the specific filings, using each filing's own
  fiscal year from its cover page. A question about a period that isn't indexed gets no
  passages and an abstention, never another quarter's numbers.
- **Retrieve.** Postgres full-text search (`ts_rank` over the question's words) and cosine
  vector search, 50 candidates each, merged with reciprocal rank fusion. Filters: ticker,
  form, period, section, accession.
- **Answer.** `qwen3:8b` gets numbered passages and must return JSON in which every sentence
  cites passages. Code then drops citations to passages it never supplied, removes uncited
  sentences, and flags any number missing from the cited passages. It abstains when the model
  says the passages don't answer or when nothing survives the checks.

## MCP tools

All tools are read-only.

| Tool | Returns |
| --- | --- |
| `list_coverage` | Indexed companies, filing counts, period ranges, and what is not covered |
| `search_filings` | Ranked passages with ticker, form, period, section and offsets |
| `get_passage` | A passage by chunk id, or an exact span by accession, section and offsets, with surrounding text and EDGAR URL |
| `answer_question` | A cited answer or an abstention, with the passages used |
| `compare_filings` | One section of two filings: top passages on a focus topic, or the passages that changed most |

Claude Desktop: copy `docs/claude_desktop_config.example.json` into Claude Desktop's config.

## Coverage

12 companies: NVDA, AMD, AVGO, MSFT, AMZN, GOOGL, META, INTC, MU, AMAT, LRCX, KLAC. For each,
the latest two 10-Ks and six 10-Qs, 96 filings in all. TSM, ASML and ARM file 20-F reports and
are not indexed. Intel's filings use a cross-reference index instead of Item headings, so they
are searchable as whole documents without section labels.

## Results

Measured on 2026-10-08 on the 60-item golden set: 51 answerable and 9 unanswerable questions.
Source files: `benchmarks/retrieval-2026-10-08.json`,
`benchmarks/retrieval-experiments-2026-10-08.json` and `benchmarks/eval-2026-10-08.json`.

**Read these numbers with two caveats.**
- No golden item has been hand-checked yet. Items were drafted from filing text by an agent,
  and code checks that every gold quote matches the filing exactly.
- Query scoping and keyword ranking were tuned on this same set. Its questions use the
  phrasings the scope parser handles ("most recent 10-Q", "fiscal 2026 10-K", "quarter ended
  …") and always name tickers. Expect lower numbers on questions written by someone else.

**Retrieval** (51 answerable items; a hit is a retrieved chunk overlapping a gold sentence)

| Configuration | Recall@5 | Recall@10 | MRR |
| --- | --- | --- | --- |
| Hybrid, nomic-embed-text, query scoping (shipped) | 0.59 | 0.75 | 0.48 |
| Same, without query scoping | 0.38 | 0.45 | 0.31 |
| Same, oracle filter to the gold filings (upper bound for scoping) | 0.60 | 0.76 | 0.48 |
| Hybrid, mxbai-embed-large, query scoping | 0.60 | 0.75 | 0.47 |
| Vector only, nomic-embed-text | 0.49 | 0.66 | 0.40 |
| Vector only, mxbai-embed-large | 0.54 | 0.73 | 0.43 |
| Keyword only | 0.51 | 0.68 | 0.38 |

- Query scoping is the largest gain, and it comes close to the oracle on this set.
- mxbai is the better model for vector-only search. In hybrid mode the two tie.
- nomic was kept because it embeds about twice as fast and has smaller vectors.

**Answers** (qwen3:8b, k = 8 passages)

| Metric | Result | Proposed target |
| --- | --- | --- |
| Abstention accuracy (unanswerable) | 1.00 (9/9) | ≥ 0.80 |
| False abstention (answerable) | 0.29 (15/51) | ≤ 0.10 |
| Citation precision | 0.69 | ≥ 0.90 |
| Answers with a number missing from cited passages | 1 (3 numbers) | 0 |
| Answer agreement, local 8B judge | 0.57 | indicative only |

- **False abstention.** In 11 of the 15 cases, none of the 8 passages the model saw contained
  a gold sentence, so abstaining matched the evidence. The other 4 are model misses.
- **Comparison questions** cause most of the retrieval misses. Both filings are retrieved, but
  the sentences that changed rank low inside each filing.
- **Abstention.** Unanswerable questions went from 8/9 to 9/9 after one rule change: a question
  that names a period with no indexed filing now gets no passages. A perfect abstention rate
  next to a 29% false-abstention rate means the system leans toward declining.
- **Citation precision.** Four answers cited no gold passage at all (lookup-005, lookup-009,
  risk-005, comparison-003). Three cross-company answers cited many non-gold passages.
- **Number check.** It compares normalized numbers ("60.0" equals "60") and ignores
  source names such as "fiscal 2026" and product codes such as "MI308". An earlier, stricter
  version flagged 5 answers on the same model output. The one answer still flagged
  (comparison-009) converts units and computes a difference, which the check is meant to catch.
- **Judge.** The local judge is lenient. It marked risk-005 correct even though the answer
  cited no gold passage. Treat its rate as a rough signal until a human spot-check exists.

**CI gate.** The fixture corpus is 11 filings (1,584 chunks) and 36 golden questions, with
precomputed vectors. The baseline is Recall@10 0.741 and MRR 0.462. With vector search
dropped, the gate measured Recall@10 0.662 and MRR 0.361 and failed.
`tests/test_gate.py` checks that this degradation fails the gate, and runs in GitHub Actions on
every push.

## Run it

Prerequisites are Docker, Ollama, and uv.

```powershell
python -m uv sync
docker compose up -d --wait
.venv\Scripts\alembic upgrade head
copy .env.example .env               # set SEC_USER_AGENT to your name and email
ollama pull nomic-embed-text; ollama pull qwen3:8b
.venv\Scripts\filings ingest         # about 100 filings, several minutes
.venv\Scripts\filings embed
.venv\Scripts\filings answer "Does Micron's latest 10-K list customer concentration as a risk factor?" --tickers MU
```

Checks (the same steps CI runs): `powershell -File scripts\check.ps1`.
Full evaluation: `powershell -File scripts\eval.ps1`.
