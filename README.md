# Goldirham Filings

Ask questions about the SEC 10-K and 10-Q filings of AI-infrastructure companies and get
answers that cite the exact filing passages, or an explicit "the filings don't answer this".
It runs as an MCP server, so Claude Desktop or any MCP client can call it. Everything runs on
your machine: EDGAR is free, and embeddings and answers come from local models through Ollama.

It summarizes what filings say. It is not investment advice.

## Quickstart (Windows)

You need [Python 3.11+](https://www.python.org/downloads/),
[Docker Desktop](https://www.docker.com/products/docker-desktop/) (running) and
[Ollama](https://ollama.com/download) (running). The two models, the Postgres image and the
database take about 7 GB of disk.

```powershell
git clone https://github.com/ixtaqq/goldirham-filings.git
cd goldirham-filings
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

`setup.ps1` installs the Python environment, starts Postgres in Docker, creates `.env` (it asks
for your name and email, which SEC requires in every request), pulls `qwen3:8b` and
`nomic-embed-text`, indexes 96 filings of 12 companies, loads their XBRL figures and runs
`filings doctor`. The first run takes about 6 minutes on a machine with a GPU, plus the
downloads. It is safe to rerun: an existing `.env` is never changed and indexed filings are
skipped.

### Connect Claude Desktop

1. Print the config for your machine:
   ```powershell
   .venv\Scripts\filings mcp-config
   ```
2. Open Claude Desktop, then Settings, Developer, Edit Config. That opens
   `%APPDATA%\Claude\claude_desktop_config.json`. Paste the `goldirham-filings` entry inside
   `mcpServers` (keep any servers already there) and save.
3. Quit Claude Desktop fully (also from the tray icon) and start it again.
4. In a new chat, open the tools menu: `goldirham-filings` should list six tools.
5. Ask: "Using goldirham-filings, what did NVIDIA report as Data Center revenue in its 10-Q
   for the quarter ended July 26, 2026?" The answer should cite passages from that 10-Q.

If the server doesn't appear, Settings, Developer shows its log. Docker and Ollama must be
running whenever Claude Desktop starts the server.

## MCP tools

All tools are read-only. Arguments are JSON, as an MCP client sends them.

| Tool | Returns | Example arguments |
| --- | --- | --- |
| `list_coverage` | Indexed companies, filing counts, period ranges, newest filing, when the index was last updated, and what isn't covered | `{}` |
| `search_filings` | Ranked passages with ticker, form, period, section and offsets | `{"query": "export controls China", "tickers": ["AMAT"], "k": 3}` |
| `get_passage` | A passage by chunk id, or an exact span by accession, section and offsets, with surrounding text and the EDGAR URL | `{"accession": "0001045810-26-000075", "section": "I-2", "start_char": 17134, "end_char": 17279}` |
| `answer_question` | A cited answer or an abstention, with the passages used | `{"question": "Does Micron's fiscal 2025 10-K list customer concentration as a risk factor?", "tickers": ["MU"]}` |
| `compare_filings` | One section of two filings: top passages on a focus topic, or the passages that changed most. Works across a 10-K and a 10-Q | `{"ticker": "MSFT", "section": "1A", "k": 3}` |
| `get_financial_fact` | An exact reported XBRL value with unit, period, duration and source filing | `{"ticker": "AMD", "concept": "revenue", "fiscal_year": 2026, "fiscal_period": "Q3"}` |

Section ids are 10-K Items (`1A` risk factors, `7` MD&A) and 10-Q Items (`II-1A`, `I-2`).
`get_financial_fact` accepts XBRL concept names (`NetIncomeLoss`) or plain names such as
`revenue`, `net income` or `capital expenditures`. A quarter returns every duration that ends
on its date (quarter and year to date); nothing is computed.

## Everyday commands

| Task | Command |
| --- | --- |
| Check every dependency | `.venv\Scripts\filings doctor` |
| Pick up new filings (then embed and refresh XBRL) | `.venv\Scripts\filings update` |
| Ask from the terminal | `.venv\Scripts\filings answer "question" --tickers MU` |
| Search from the terminal | `.venv\Scripts\filings search "question" --tickers MU` |
| Index other tracked companies | set `INGEST_TICKERS` in `.env`, then `filings ingest`, `filings embed`, `filings facts` |

To keep the index current, schedule `filings update`, for example daily with Windows Task
Scheduler:

```powershell
schtasks /Create /TN "Goldirham Filings update" /SC DAILY /ST 06:30 /TR "<path>\.venv\Scripts\filings.exe update"
```

## Troubleshooting

`filings doctor` names the failing piece; every tool and command prints one of these messages
instead of a traceback.

| Message | Fix |
| --- | --- |
| `The database at 127.0.0.1:5442/filings is not reachable` | Start Docker Desktop, then `docker compose up -d --wait` in the project folder. |
| `The database ... has no filing tables` | `.venv\Scripts\alembic upgrade head`, then `filings ingest`. |
| `Ollama is not reachable at http://127.0.0.1:11434` | Install Ollama from ollama.com and start it (it runs in the tray). |
| `Ollama model qwen3:8b is not pulled` | `ollama pull qwen3:8b` (or the model named). |
| `Ollama ... did not respond in time` | The model is loading or the machine is busy; try again. `ollama ps` shows what is loaded. |
| `No chunk has a nomic-embed-text vector` / `... chunks have no ... vector` | `filings embed`. Search works by keyword alone until then. |
| `SEC refused the request (HTTP 403)` | Set `SEC_USER_AGENT` in `.env` to your real name and email. SEC blocks generic agents. |
| `No XBRL facts loaded for ...` | `filings facts`. |

## Coverage

12 companies: NVDA, AMD, AVGO, MSFT, AMZN, GOOGL, META, INTC, MU, AMAT, LRCX, KLAC. For each,
the latest two 10-Ks and six 10-Qs as of the first ingest, plus anything `filings update`
finds later. Twenty more tracked US companies (QCOM, ORCL, IBM, SMCI, DELL, CSCO, MRVL, ANET,
DLR, EQIX, CEG, VST, GEV, VRT, ETN, WDC, ON, STX, ANSS, SNPS) can be added through
`INGEST_TICKERS`; measured cost for all twenty is about 2 minutes of ingest, 5 minutes of
embedding and 180 MB (`docs/decisions.tsv`, phase 3).

## Limitations

- **TSM, ASML and ARM aren't covered.** They file 20-F and 6-K reports. Support is scoped in
  `docs/decisions.tsv` but not built.
- **Some filings have no sections.** Intel's 10-Ks, and IBM, Eaton and Digital Realty filings
  if you add them, are searched as whole documents because their text has no Item headings.
  Section filters still include them.
- **Comparisons are answered side by side.** Each filing or company is answered on its own,
  and the answer doesn't compare the parts itself.
- **The answer model is local and small** (`qwen3:8b`). It wrongly declined 2 of 21 answerable
  held-out questions and about 1 in 4 on dev (see Results), and the judge used in evaluation
  is the same size.
- **Most evaluation questions are unchecked.** The 25 held-out questions behind the v1.0
  numbers were checked by hand; the 68 golden dev and test questions were not, so their
  numbers are provisional.
- **Figures can come from the wrong column or duration.** An answer can quote a prior-year or
  nine-month figure as the quarter's; the checks pass because the figure is in the passage.

## How it works

```
EDGAR ─► ingest ─► sections (Item 1A, 7, ...) ─► chunks ─► Postgres full-text + pgvector
MCP client ─► MCP server ─► scoped hybrid search ─► per-part answers ─► checks ─► cited answer
golden set ─► eval harness ─► CI retrieval gate + dated reports in benchmarks/
```

- **Ingest.** A polite EDGAR client (your contact in `User-Agent`, at most 6 requests a second,
  retries with backoff) fetches each filing once into `data/raw/`. A filing that fails to
  download or parse is reported and skipped.
- **Parse.** HTML becomes text with table cells joined by ` | `. Item headings split it into
  sections. Filers without Item headings fall back to their own headings (Intel 10-Qs) or to
  one whole-document section.
- **Chunk.** About 500 tokens, never across a section, with offsets such that
  `section.text[start:end] == chunk.text`.
- **Scope.** Wording such as "fiscal 2025 10-K", "last two 10-Qs" or "quarter ended June 27,
  2026" picks the filings, using each filing's own fiscal year. A named section ("risk
  factors", "MD&A") picks the section in both forms. A period that isn't indexed gets an
  abstention, never another quarter's numbers.
- **Retrieve.** Postgres full-text and cosine vector search, merged by reciprocal rank fusion,
  searched per filing when several are named, with a mild preference for newer filings when
  none is.
- **Answer.** A question about several filings or companies is answered one part at a time,
  and each part can decline. `qwen3:8b` returns JSON in which every sentence cites passages.
  Code drops invalid citations and uncited sentences, removes sentences with numbers not in
  their cited passages, and flags period names or dates that match neither the cited filing
  nor its text.
- **Figures.** XBRL company facts give exact reported values through `get_financial_fact`.

## Results

The v1.0 numbers come from a **held-out set** of 25 questions (`evals/heldout.jsonl`): drafted
from filings no tuning run had used, checked by hand, and run once. The **golden set**
(`evals/golden.jsonl`) has a dev split for tuning (48 questions, 42 answerable) and a test
split (20); `evals/split.json` fixes the split. Every number comes from the named file in
`benchmarks/`.

| Metric | Held-out v1 | Dev Oct | Dev v1 | Test Oct | Test Nov | Target | Met (held-out) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Recall@10 | 0.833 | 0.730 | 0.742 | 0.784 | 0.873 | ≥ 0.85 | no |
| MRR | 0.525 | 0.441 | 0.500 | 0.502 | 0.491 | | |
| Unanswerable declined | 2/4 | 6/6 | 6/6 | 3/3 | 2/3 | ≥ 0.80 | no |
| Wrongly declines answerable | 0.095 (2/21) | 0.286 | 0.238 | 0.176 | 0.235 | ≤ 0.10 | yes |
| Citation precision | 0.614 | 0.678 | 0.662 | 0.774 | 0.741 | ≥ 0.90 | no |
| Answers with unsupported numbers | 0 | 3 | 0 | 1 | 0 | 0 | yes |
| Judge agreement (local 8B) | 0.714 | 0.595 | 0.643 | 0.588 | 0.471 | indicative | |

Files: held-out `retrieval-all-2026-11-06-heldout-v1.json`, `eval-all-2026-11-06-heldout-v1.json`;
dev Oct `retrieval-dev-2026-10-09-final.json`, `eval-dev-2026-10-09-final2.json`; dev v1
`retrieval-dev-2026-11-06-v1-final-spans.json`, `eval-dev-2026-11-06-v1-final-spans.json`;
test Oct `retrieval-test-2026-10-09-final.json`, `eval-test-2026-10-09-final2.json`; test Nov
`retrieval-test-2026-11-06-phase2.json`, `eval-test-2026-11-06-phase2.json` (run before the
Intel parser change).

**Where v1.0 falls short, on the held-out set.**
- **Unanswerable questions: 2 of 4 declined, target 0.80.** With 4 unanswerable questions the
  floor requires 4 of 4 (3 of 4 is 0.75). The miss is a real signal, but on a small sample.
  - `heldout-unanswerable-002` asked for Meta's fourth-quarter 2026 Reality Labs loss, which
    no filing reports. The answer gave the third-quarter loss ($4,129 million, from the 10-Q
    table) as the fourth quarter. The period check flagged "fourth quarter of 2026", but a
    flag only annotates the answer; it doesn't remove the sentence.
  - `heldout-unanswerable-003` asked for Lam's headcount at December 31, 2026. The answer
    gave the dated figures the filings do have (September 27, August 4, March 29) and
    didn't claim a December figure, which the item's own review note allows. The metric
    counts any answer to an unanswerable question as a failure; v1.0 is not re-scored for
    this.
- **Recall@10 0.833, target 0.85.** Comparisons are the weak type (Recall@10 0.167 on 3
  questions): the older filing's passage ranks low.
- **Citation precision 0.614, target 0.90.** Part of the gap is the metric: correct answers
  that cite the same fact stated in another section score 0, as in `heldout-lookup-003` (the
  notes repeat the MD&A sentence) and `heldout-lookup-004` (Part II Item 2 repeats it). The
  restatements aren't checked alternate spans, and v1.0 is not re-scored for them.
- **A figure can come from the wrong duration.** `heldout-lookup-009` reported Meta's
  nine-month capital expenditures ($85.82 billion) as the quarter's. The figure is in the
  passage and the period matches the filing, so neither check catches it.
- **Comparisons are answered side by side.** Of three held-out comparisons, one declines
  entirely and two decline one side; none compares the two filings.

**Met on the held-out set:** wrong declines 0.095 (target 0.10) and no answers with
unsupported numbers. That check covers numbers, not periods or durations: `heldout-lookup-009`
passed it while reporting a nine-month figure as the quarter's.

The fixes for these gaps are planned and will be measured on dev, with a new held-out set:
`docs/v1.1-plan.md`.

**Caveats.**
- Small sets: one item moves a held-out rate by 4 to 25 points (21 answerable, 4
  unanswerable questions).
- The held-out answers were read after the run to explain these failures. Any change made
  because of them has to be measured on dev; the held-out set is no longer clean for it.
- The held-out questions were drafted by the same model family that built the system, then
  checked by a person. The golden dev and test questions are not checked yet.
- The test split is no longer clean (five logged runs, two followed by a look at a failing
  item), and `unanswerable-008` became answerable after the November re-ingest.
- October and November numbers come from different indexes (nine newer filings, ten
  questions reworded to name their period). On the same November index before Phase 2, dev
  Recall@10 was 0.710 (`retrieval-dev-2026-11-06-pinned-periods.json`).

**What moved the numbers (dev):** naming a section filters to it (Recall@10 0.710 → 0.742),
answering per part (wrong declines 12/42 → 10/42), and removing sentences with unsupported
numbers (3 → 0). Rejected ideas, such as a cross-encoder reranker and change-based ranking
for comparisons, are in `docs/decisions.tsv` with their numbers.

**CI gate.** The fixture corpus is 11 filings (1,584 chunks) and 39 golden questions with
precomputed vectors. Baseline: Recall@10 0.812, MRR 0.530 (`evals/baseline.json`). With vector
search dropped the gate fails on both; `tests/test_gate.py` checks that in CI. GitHub Actions
runs `scripts\check.ps1`'s steps on every pull request and every push to `main`.

**MCP clients.** `scripts\inspector_check.ps1` calls all six tools through the MCP Inspector
2.14.0 CLI (`benchmarks/inspector-2026-11-06.json`); `scripts\mcp_smoke.py` checks one result
per tool through the stdio entry point.

## Development

| Task | Command |
| --- | --- |
| All checks (what CI runs) | `powershell -File scripts\check.ps1` |
| Retrieval eval on dev | `.venv\Scripts\filings eval retrieval --split dev` |
| Full eval (answers, local judge) | `powershell -File scripts\eval.ps1` |
| MCP smoke test | `.venv\Scripts\python scripts\mcp_smoke.py` |
| MCP Inspector check | `powershell -File scripts\inspector_check.ps1` |

Design: `docs/design.md`. Decisions and their evidence: `docs/decisions.tsv`. Departures from
the design: `docs/implementation-notes.md`. Changes: `CHANGELOG.md`.

## License

MIT. See `LICENSE`.
