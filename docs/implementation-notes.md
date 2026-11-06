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
  period names and dates such as "fiscal 2026", "Q2 2026" or "June 30, 2026". Those are
  checked separately against the cited filing's fiscal year, fiscal period and period end,
  or a period the passage itself names; a mismatch is flagged in `unsupported_periods`.
- **Sentences with unsupported numbers are removed**, not just flagged. On dev every such
  sentence was a computed or invented figure; removing them cost two declines, both on
  answers that were not correct (`benchmarks/eval-dev-2026-11-06-phase1-drop.json`).
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
- **Intel filings** use a cross-reference index instead of Item headings. Since parser v4
  its 10-Qs are split on Intel's own headings (`parse.OWN_HEADINGS`). Its 10-Ks have no
  risk-factors heading in their text, so they still index as one `unknown` section.
- **The digest's company list had six wrong CIKs**: TSM, KLAC, DLR, EQIX, GEV and VRT. They are
  corrected here and checked by `scripts/check_companies.py`. `ai-infra-digest` itself is
  unchanged.

- **Dev/test split** (`evals/split.json`, seed 20261008, stratified by type). Tuning uses dev
  only, and every test run is logged. Eight dev-only recency questions (lookup-101 to 108)
  were added later. They are unchecked and never part of test.
- **Per-filing retrieval.** When scope names two or more filings, each filing is searched
  separately and the rankings are interleaved round-robin (`retrieve.balance_groups`,
  `interleave`).
- **Recency preference.** When neither the question nor the caller names filings or periods,
  each fused rank score is multiplied by 0.9 for every newer filing the same company has
  (`retrieve.prefer_recent`).
- **Answer prompt.** Two rules were added. The model cites only passages that directly state
  the fact, and it does not calculate new figures. Exact repeated sentences are dropped in code.
- **The partial-answer rule was tried and reverted.** It let the model answer the covered
  parts of multi-part questions. It cut dev wrong declines but answered an unanswerable test
  question, breaking the unanswerable floor.

The v1.0 work (`docs/decisions.tsv`, phases tagged `v1-phase0` to `v1-phase4`) added:

- **Questions answered per part.** Several named filings or companies get one search and one
  model call each; a part may decline on its own. This replaced the reverted prompt rule.
- **Section intent.** A named section ("risk factors", "MD&A") filters search to it.
- **Answer checks.** Sentences with numbers not in their cited passages are removed, and
  period names and dates are checked against the cited filing's fiscal period.
- **Dependency errors.** Ollama and database failures become one-sentence messages
  (`errors.py`).
- **Freshness and figures.** `filings update` with a run log, and XBRL company facts behind
  `get_financial_fact`, which never computes a value.
- **Golden-set maintenance.** Ten time-relative questions name their period; alternate spans
  are supported but credited only once checked.

## Not done in this pass

- **Human checks.** The 25 held-out questions are checked by the owner. No golden item is
  (`checked_by` is null on all 68 and the 8 drafted alternate spans), and there is no human
  spot-check of 20 answers, so dev and test metrics and the CI baseline are provisional.
- **Claude Desktop.** The server was verified through the MCP Inspector CLI
  (`benchmarks/inspector-2026-11-06.json`) and `scripts/mcp_smoke.py`, not yet from Claude
  Desktop itself; the README lists the steps.
- **Reranker.** Measured and rejected: bge-reranker-base on CPU gained 0.5 MRR points (the bar
  was 3) and cost recall (`benchmarks/rerank-dev-2026-10-08-bge-reranker-base.json`).
- **bge-m3 on the final chunks.** bge-m3 had the worst hybrid scores on the earlier chunks, so
  it was not re-embedded after the chunker fix.
- **Multi-company questions without a filing reference.** Per-company retrieval was tried
  and reverted. Dev has only one such question, so it couldn't be measured.
- **Within-filing ranking for comparisons.** Comparison questions retrieve both filings but
  rank the older side's gold low. Favoring text that differs from the paired filing was
  measured and not built: gold is no more different than its rivals
  (`benchmarks/novelty-dev-2026-11-06.txt`).
- **20-F filers.** TSM, ASML and ARM support is scoped (about a day and a half) but not built.
- **Held-out targets.** The one held-out run (25 owner-checked questions) missed the
  unanswerable, Recall@10 and citation-precision targets
  (`benchmarks/eval-all-2026-11-06-heldout-v1.json`). A candidate fix, removing sentences
  with unsupported periods, is noted in `docs/decisions.tsv` but not measured or applied.
- **Demo, CV and portfolio.** The demo recording and the CV and portfolio updates belong to
  the owner.
