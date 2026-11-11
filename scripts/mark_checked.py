"""Record a person's review of golden items: sets checked_by and checked_at on the named ids.

Only a human runs this, after reading each item against its filing; agents draft items but
never mark them checked. Other items and fields are left exactly as they are. With
--alternates, each id is ITEM:INDEX and only that alternate span is marked.

    .venv\\Scripts\\python scripts\\mark_checked.py evals\\heldout.jsonl --by "Your Name" ID ...
    .venv\\Scripts\\python scripts\\mark_checked.py evals\\golden.jsonl --by "Your Name" \\
        --alternates lookup-003:0 ...
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from goldirham_filings.golden import (
    AlternateSpan,
    GoldItem,
    load_golden,
    mark_alternates,
    save_golden,
)


def mark_items(items: list[GoldItem], ids: list[str], by: str, on: date) -> list[GoldItem]:
    unknown = sorted(set(ids) - {i.id for i in items})
    if unknown:
        sys.exit(f"not found: {', '.join(unknown)}")
    return [
        i.model_copy(update={"checked_by": by, "checked_at": on}) if i.id in ids else i
        for i in items
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--by", required=True, help="your name, recorded as checked_by")
    parser.add_argument("--alternates", action="store_true", help="ids are ITEM:INDEX")
    parser.add_argument("ids", nargs="+")
    args = parser.parse_args()
    items = load_golden(args.path)
    today = date.today()
    if args.alternates:
        refs = [(ref.rsplit(":", 1)[0], int(ref.rsplit(":", 1)[1])) for ref in args.ids]
        try:
            marked = mark_alternates(items, refs, args.by, today)
        except ValueError as error:
            sys.exit(str(error))
        reviewed: Sequence[GoldItem | AlternateSpan] = [
            a for i in marked for a in i.alternate_spans
        ]
    else:
        marked = mark_items(items, args.ids, args.by, today)
        reviewed = marked
    save_golden(args.path, marked)
    checked = sum(r.checked_by is not None for r in reviewed)
    print(f"marked {len(set(args.ids))} checked by {args.by}; {checked}/{len(reviewed)} checked")


if __name__ == "__main__":
    main()
