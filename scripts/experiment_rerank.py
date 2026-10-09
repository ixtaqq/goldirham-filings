"""Experiment: cross-encoder rerank of the top candidates (design open question 3). Dev only.

Reranks the shipped hybrid top-N with a local sentence-transformers cross-encoder, keeping
per-filing interleaving when the question names several filings, and records dev retrieval
metrics and per-query latency. Writes benchmarks/rerank-dev-YYYY-MM-DD-<model>.json.
Needs the optional sentence-transformers dependency.
"""

import argparse
import json
import time
from datetime import date

from sentence_transformers import CrossEncoder

from goldirham_filings.config import ROOT, settings
from goldirham_filings.db import connect
from goldirham_filings.embed import OllamaEmbedder
from goldirham_filings.evaluate import rows, score_retrieval, summarize_retrieval
from goldirham_filings.golden import load_golden, select
from goldirham_filings.retrieve import Filters, Retriever, interleave


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="BAAI/bge-reranker-base")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()
    cfg = settings()
    split = json.loads((ROOT / "evals" / "split.json").read_text(encoding="utf-8"))
    items = [i for i in select(load_golden(ROOT / "evals" / "golden.jsonl"), split, "dev")
             if i.answerable]  # fmt: skip
    encoder = CrossEncoder(args.model, max_length=512)
    scores, latencies = [], []
    with connect() as conn:
        retriever = Retriever(conn, OllamaEmbedder(cfg.embedding_model, cfg.ollama_url))
        for item in items:
            candidates = retriever.search(item.question, item.filters, k=args.top)
            start = time.perf_counter()
            relevance = encoder.predict([(item.question, p.text) for p in candidates])
            latencies.append(time.perf_counter() - start)
            reranked = [p for _, p in sorted(zip(relevance, candidates, strict=True),
                                             key=lambda pair: -float(pair[0]))]  # fmt: skip
            scoped = retriever.scoped(item.question, item.filters) or Filters()
            groups = scoped.accessions if scoped.accessions and len(scoped.accessions) > 1 else []
            if groups:
                order = interleave([[p.chunk_id for p in reranked if p.accession == a]
                                    for a in groups])  # fmt: skip
                by_id = {p.chunk_id: p for p in reranked}
                reranked = [by_id[i] for i in order]
            scores.append(score_retrieval(item, reranked[:10]))
    latencies.sort()
    report = {
        "reranker": args.model, "top": args.top, "summary": summarize_retrieval(scores),
        "rerank_seconds_median": round(latencies[len(latencies) // 2], 3),
        "rerank_seconds_max": round(latencies[-1], 3), "items": rows(scores),
    }  # fmt: skip
    name = args.model.split("/")[-1]
    out = ROOT / "benchmarks" / f"rerank-dev-{date.today().isoformat()}-{name}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report["summary"], report["rerank_seconds_median"], report["rerank_seconds_max"])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
