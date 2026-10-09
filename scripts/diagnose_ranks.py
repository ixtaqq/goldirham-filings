"""Where gold sentences rank, for multi-filing questions on the dev split.

For each gold span of each dev comparison and cross-company item: its rank in the combined
hybrid search (as shipped), and its rank in a search restricted to its own filing. Writes
benchmarks/ranks-dev-YYYY-MM-DD[-label].json. Rerunnable; reads only the dev split.
"""

import argparse
import json
from datetime import date

from goldirham_filings.config import ROOT, settings
from goldirham_filings.db import connect
from goldirham_filings.embed import OllamaEmbedder
from goldirham_filings.evaluate import span_hit
from goldirham_filings.golden import GoldSpan, load_golden, select
from goldirham_filings.retrieve import Passage, Retriever

DEPTH = 50


def rank(passages: list[Passage], span: GoldSpan) -> int | None:
    return next((n for n, p in enumerate(passages, 1) if span_hit(p, span)), None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    cfg = settings()
    split = json.loads((ROOT / "evals" / "split.json").read_text(encoding="utf-8"))
    items = [
        i for i in select(load_golden(ROOT / "evals" / "golden.jsonl"), split, "dev")
        if i.type in ("comparison", "cross-company")
    ]  # fmt: skip
    rows = []
    with connect() as conn:
        retriever = Retriever(conn, OllamaEmbedder(cfg.embedding_model, cfg.ollama_url),
                              candidates=DEPTH)  # fmt: skip
        for item in items:
            combined = retriever.search(item.question, item.filters, k=DEPTH)
            for span in item.gold_spans:
                own = retriever.search(
                    item.question,
                    item.filters.model_copy(update={"accessions": [span.accession]}),
                    k=DEPTH,
                )
                rows.append({
                    "item_id": item.id, "type": item.type, "accession": span.accession,
                    "section": span.section, "rank_combined": rank(combined, span),
                    "rank_in_own_filing": rank(own, span),
                    "filings_in_top8": len({p.accession for p in combined[:8]}),
                })  # fmt: skip
                print(rows[-1])
    name = "-".join(p for p in ("ranks-dev", date.today().isoformat(), args.label) if p)
    out = ROOT / "benchmarks" / f"{name}.json"
    out.write_text(json.dumps({"depth": DEPTH, "rows": rows}, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
