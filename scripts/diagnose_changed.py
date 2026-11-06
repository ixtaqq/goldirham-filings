"""Could the changed-passages channel find comparison gold sentences? Dev split only.

For each dev comparison item whose two gold spans are in two filings of one ticker: is the
newer filing's gold chunk among the k chunks that changed most versus the older filing
(the matching section of each form)? Writes benchmarks/changed-dev-YYYY-MM-DD.json.
"""

import json
from datetime import date

from goldirham_filings.config import ROOT, settings
from goldirham_filings.db import connect
from goldirham_filings.evaluate import span_hit
from goldirham_filings.golden import load_golden, select
from goldirham_filings.retrieve import changed_passages, passages_by_id

K = 8


def main() -> None:
    cfg = settings()
    split = json.loads((ROOT / "evals" / "split.json").read_text(encoding="utf-8"))
    items = [
        i for i in select(load_golden(ROOT / "evals" / "golden.jsonl"), split, "dev")
        if i.type == "comparison"
    ]  # fmt: skip
    rows = []
    with connect() as conn:
        for item in items:
            spans = sorted(item.gold_spans, key=lambda s: s.accession)
            dates = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT accession, period_end FROM filings WHERE accession = ANY(%s)",
                    ([s.accession for s in spans],),
                ).fetchall()
            }
            older, newer = sorted(spans, key=lambda s: dates[s.accession])
            changed = changed_passages(conn, cfg.embedding_model, older.accession,
                                       newer.accession, older.section, newer.section,
                                       K)  # fmt: skip
            newer_ids = [c.newer.chunk_id for c in changed]
            hit = any(span_hit(p, newer) for p in passages_by_id(conn, newer_ids))
            rows.append({
                "item_id": item.id, "newer_section": newer.section,
                "older_section": older.section, "newer_gold_in_changed_top_k": hit,
            })  # fmt: skip
            print(rows[-1])
    out = ROOT / "benchmarks" / f"changed-dev-{date.today().isoformat()}.json"
    out.write_text(json.dumps({"k": K, "rows": rows}, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
