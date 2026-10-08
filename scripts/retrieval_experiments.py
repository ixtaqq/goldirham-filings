"""Rerunnable retrieval experiments behind the README's scoping claims.

Hybrid retrieval on the dev index with the configured embedding model, three ways:
without query scoping, with it (shipped), and with an oracle filter to the gold filings
(an upper bound for scoping). Writes benchmarks/retrieval-experiments-YYYY-MM-DD.json.
"""

import json
from datetime import date
from pathlib import Path

from goldirham_filings.config import ROOT, settings
from goldirham_filings.db import connect
from goldirham_filings.embed import OllamaEmbedder
from goldirham_filings.evaluate import rows, run_retrieval, summarize_retrieval
from goldirham_filings.golden import load_golden
from goldirham_filings.retrieve import Retriever


def main() -> None:
    cfg = settings()
    items = [i for i in load_golden(ROOT / "evals" / "golden.jsonl") if i.answerable]
    oracle = [
        i.model_copy(
            update={"filters": i.filters.model_copy(
                update={"accessions": sorted({s.accession for s in i.gold_spans})}
            )}
        )
        for i in items
    ]  # fmt: skip
    embedder = OllamaEmbedder(cfg.embedding_model, cfg.ollama_url)
    report: dict[str, object] = {"embedding_model": cfg.embedding_model, "mode": "hybrid"}
    with connect() as conn:
        for name, scope, golden in (
            ("no_scoping", False, items),
            ("scoping", True, items),
            ("oracle_filing_filter", False, oracle),
        ):
            scores = run_retrieval(Retriever(conn, embedder, scope_from_query=scope), golden)
            report[name] = {"summary": summarize_retrieval(scores), "items": rows(scores)}
            print(f"{name:22} {summarize_retrieval(scores)}")
    out = Path(ROOT / "benchmarks" / f"retrieval-experiments-{date.today().isoformat()}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
