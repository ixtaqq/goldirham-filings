"""Record a person's review of golden items: sets checked_by and checked_at on the named ids.

Only a human runs this, after reading each item against its filing; agents draft items but
never mark them checked. Other items and fields are left exactly as they are.

    .venv\\Scripts\\python scripts\\mark_checked.py evals\\heldout.jsonl --by "Your Name" ID ...
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from goldirham_filings.golden import load_golden, save_golden


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--by", required=True, help="your name, recorded as checked_by")
    parser.add_argument("ids", nargs="+")
    args = parser.parse_args()
    items = load_golden(args.path)
    unknown = sorted(set(args.ids) - {i.id for i in items})
    if unknown:
        sys.exit(f"not in {args.path}: {', '.join(unknown)}")
    today = date.today()
    marked = [
        i.model_copy(update={"checked_by": args.by, "checked_at": today}) if i.id in args.ids else i
        for i in items
    ]
    save_golden(args.path, marked)
    checked = sum(i.checked_by is not None for i in marked)
    print(
        f"marked {len(set(args.ids))} items checked by {args.by}; {checked}/{len(marked)} checked"
    )


if __name__ == "__main__":
    main()
