"""Answer evaluation on the CI fixture corpus: dev-split answerable items only.

For measuring an answer change without the dev index. The fixture has question vectors for
answerable items alone; --unanswerable reads abstention accuracy with keyword retrieval
instead, over a corpus of only five companies. Needs Ollama with the answer model. Writes
benchmarks/eval-fixture-dev-<date>-<label>.json, comparable with scripts/compare_reports.py.

  python scripts/eval_fixture.py --label periods-on --drop-periods
"""

import argparse
import json
import os
from dataclasses import asdict
from datetime import date

from goldirham_filings.answer import Answerer, OllamaChat
from goldirham_filings.config import ROOT, settings
from goldirham_filings.db import connect
from goldirham_filings.evaluate import rows, run_answers, run_retrieval, summarize_answers
from goldirham_filings.fixtures import FixtureEmbedder
from goldirham_filings.golden import load_golden, select
from goldirham_filings.retrieve import Retriever


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--drop-periods", action="store_true")
    parser.add_argument(
        "--unanswerable",
        action="store_true",
        help="dev-split unanswerable items with keyword retrieval: they have no question vectors",
    )
    args = parser.parse_args()
    cfg = settings()
    embedder = FixtureEmbedder()
    inside = set(embedder.accessions)
    split = json.loads((ROOT / "evals" / "split.json").read_text(encoding="utf-8"))
    dev = select(load_golden(ROOT / "evals" / "golden.jsonl"), split, "dev")
    items = (
        [i for i in dev if not i.answerable]
        if args.unanswerable
        else [i for i in dev if i.answerable and all(s.accession in inside for s in i.gold_spans)]
    )
    chat = OllamaChat(cfg.answer_model, cfg.ollama_url)
    with connect(os.environ["TEST_DATABASE_URL"], read_only=True) as conn:
        retriever = Retriever(conn, embedder, mode="keyword" if args.unanswerable else "hybrid")
        answerer = Answerer(retriever, chat, drop_unsupported_periods=args.drop_periods)
        retrieval = run_retrieval(retriever, items)
        answers = run_answers(answerer, chat, items)
    summary = summarize_answers(answers)
    report = {
        "config": {"answer_model": cfg.answer_model, "drop_periods": args.drop_periods},
        "corpus": "CI fixture corpus, dev-split "
        + ("unanswerable items, keyword retrieval" if args.unanswerable else "answerable items"),
        "answers": summary,
        "retrieval_items": [asdict(s) for s in retrieval],
        "answer_items": rows(answers),
    }
    out = ROOT / "benchmarks" / f"eval-fixture-dev-{date.today().isoformat()}-{args.label}.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
