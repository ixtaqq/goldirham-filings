"""Compare saved eval reports on the same item set.

Recomputes retrieval and answer summaries from each report's per-item rows, restricted to
the items every report shares (optionally only ids with a given prefix, or excluding the
split's dev-only additions). Writes benchmarks/compare-<label>.json.

  python scripts/compare_reports.py --label item3 benchmarks/eval-dev-...-a.json ...-b.json
"""

import argparse
import json
from pathlib import Path
from typing import Any

from goldirham_filings.config import ROOT
from goldirham_filings.evaluate import (
    AnswerScore,
    RetrievalScore,
    summarize_answers,
    summarize_retrieval,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--only", help="keep item ids in this comma-separated list")
    parser.add_argument("--exclude-dev-additions", action="store_true")
    args = parser.parse_args()
    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in args.reports]
    shared = set.intersection(*({r["item_id"] for r in rep["answer_items"]} for rep in loaded))
    if args.only:
        shared &= set(args.only.split(","))
    if args.exclude_dev_additions:
        split = json.loads((ROOT / "evals" / "split.json").read_text(encoding="utf-8"))
        shared -= set(split.get("dev_only_additions", {}).get("ids", []))
    result: dict[str, Any] = {"items": sorted(shared), "reports": {}}
    for path, rep in zip(args.reports, loaded, strict=True):
        retrieval = [RetrievalScore(**r) for r in rep["retrieval_items"] if r["item_id"] in shared]
        answers = [AnswerScore(**a) for a in rep["answer_items"] if a["item_id"] in shared]
        result["reports"][path.name] = {
            "retrieval": summarize_retrieval(retrieval) if retrieval else None,
            "answers": summarize_answers(answers),
        }
        print(path.name, json.dumps(result["reports"][path.name]["answers"])[:400])
    out = ROOT / "benchmarks" / f"compare-{args.label}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(shared)} items)")


if __name__ == "__main__":
    main()
