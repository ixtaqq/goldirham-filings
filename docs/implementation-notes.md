# Implementation notes

Where the first implementation departs from `design.md`, and why. The decision log is
`docs/decisions.tsv`. Measured numbers are in `benchmarks/`.

## Changes to the design

- **Embeddings run through Ollama**, not sentence-transformers. One local runtime serves both
  embeddings and answers, and it avoids a multi-GB CUDA PyTorch install. Design 6.4 allowed either.
- **Embeddings have their own table**, keyed by `(chunk_id, model)`, instead of an `embedding`
  column on `chunks`. Comparing several models on the same chunks was a design requirement.
  The vector column has no fixed dimension, so search is an exact scan. That measured about
  100 ms per hybrid search over 12k chunks.
- **12 companies, not 10.** LRCX and KLAC were added so the semiconductor-equipment
  export-controls question covers three equipment makers.
- **Query scoping (`scope.py`) was added.** Each company has eight similar filings, and
  passages repeat from quarter to quarter. Retrieval found the right topic but in the wrong
  filing. On the golden set, hybrid Recall@10 was 0.45 without scoping and 0.75 with it. An
  oracle filter to the gold filings reaches 0.76
  (`benchmarks/retrieval-experiments-2026-10-08.json`, `scripts/retrieval_experiments.py`).
  The parser was developed on the same questions it is measured on; see the README caveats.
- **Filings store their own fiscal year and period**, read from the cover-page DEI tags
  (`dei:DocumentFiscalYearFocus` and `dei:DocumentFiscalPeriodFocus`). Fiscal years are not
  calendar years: NVIDIA's 10-Q for the quarter ended July 26, 2026 is fiscal 2027 Q2.
- **Questions about an unindexed period return nothing.** If a question names an explicit
  period end or fiscal year that no indexed filing has, search returns no passages and
  `answer_question` abstains. Before this rule, a question about a quarter that isn't indexed
  was answered with another quarter's figure.
- **The number check is not strictly verbatim** (design 6.5). It compares normalized numbers
  ("60.0" equals "60") and ignores form names, Item numbers, product codes such as MI308, and
  period names such as "fiscal 2026" or "Q2 2026". As a result, a wrong year inside
  "fiscal 2026" in an answer is not flagged.
- **The similarity abstention threshold ships off** (`abstain_min_similarity = 0`). The best cut
  on the golden set caught 2 of 9 unanswerable questions at a 4% false-abstention cost, and the
  model already abstains on 9 of 9.
- **`get_passage` takes a `section`.** Offsets index into section text, so every section
  starts at 0. Accession plus offsets alone was ambiguous. Design 6.6 lists only the accession
  and offsets.
- **`compare_filings` without a `focus`** returns the newer filing's passages that are least
  similar to anything in the older one, each paired with its closest older passage. With a
  `focus`, it returns the top passages on that topic from each filing.
- **Keyword ranking uses `ts_rank` over an OR query.** `websearch_to_tsquery` ANDs every word
  and matches almost nothing for natural-language questions. `ts_rank` beat `ts_rank_cd` on
  the golden set; that comparison was not saved as a file.
- **Intel filings** use a cross-reference index instead of Item headings. They index as one
  `unknown` section and are logged as missing their required sections.
- **The digest's company list had six wrong CIKs**: TSM, KLAC, DLR, EQIX, GEV and VRT. They are
  corrected here and checked by `scripts/check_companies.py`. `ai-infra-digest` itself is
  unchanged.

## Not done in this pass

- **Human checks.** No golden item is hand-checked yet (`checked_by` is null on all 60), and
  there is no human spot-check of 20 answers. All metrics and the CI baseline are provisional.
- **MCP clients.** The server was not tried from Claude Desktop or the MCP Inspector. The stdio
  entry point is exercised by `scripts/mcp_smoke.py`, which checks one result per tool.
- **Reranker.** Not implemented. Design open question 3 defaults to off until measured.
- **bge-m3 on the final chunks.** bge-m3 had the worst hybrid scores on the earlier chunks, so
  it was not re-embedded after the chunker fix.
- **Recency.** Questions that don't name a filing, such as "what does NVIDIA say about X",
  search all eight filings with no preference for recent ones.
- **Within-filing ranking for comparisons.** Comparison questions retrieve both filings but
  rank the changed sentences low, which drives most false abstentions.
- **Demo, CV and portfolio.** The demo recording and the CV and portfolio updates belong to
  the owner.
