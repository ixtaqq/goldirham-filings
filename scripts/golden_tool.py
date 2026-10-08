"""Read-only lookup over the index for drafting golden questions.

list [--ticker T]                         filings and their sections with sizes
grep PATTERN [--ticker T] [--form F] [--section S] [--max N]
                                          regex matches with offsets and one line of context
show ACCESSION SECTION START END          print a slice of section text
"""

import argparse
import re

from goldirham_filings.db import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("--ticker")
    p_grep = sub.add_parser("grep")
    p_grep.add_argument("pattern")
    p_grep.add_argument("--ticker")
    p_grep.add_argument("--form")
    p_grep.add_argument("--section")
    p_grep.add_argument("--max", type=int, default=20)
    p_show = sub.add_parser("show")
    p_show.add_argument("accession")
    p_show.add_argument("section")
    p_show.add_argument("start", type=int)
    p_show.add_argument("end", type=int)
    args = parser.parse_args()

    with connect() as conn:
        if args.cmd == "list":
            rows = conn.execute(
                """
                SELECT co.ticker, f.form, f.period_end, f.accession,
                       string_agg(s.item || ':' || length(s.text), ' ' ORDER BY s.id)
                FROM filings f JOIN companies co ON co.cik = f.cik
                JOIN sections s ON s.accession = f.accession
                WHERE %s::text IS NULL OR co.ticker = %s
                GROUP BY 1, 2, 3, 4 ORDER BY 1, 3 DESC
                """,
                (args.ticker, args.ticker),
            ).fetchall()
            for row in rows:
                print(*row, sep="\t")
        elif args.cmd == "grep":
            rows = conn.execute(
                """
                SELECT co.ticker, f.form, f.period_end, f.accession, s.item, s.text
                FROM sections s JOIN filings f ON f.accession = s.accession
                JOIN companies co ON co.cik = f.cik
                WHERE (%s::text IS NULL OR co.ticker = %s) AND (%s::text IS NULL OR f.form = %s)
                  AND (%s::text IS NULL OR s.item = %s) AND s.text ~* %s
                ORDER BY co.ticker, f.period_end DESC
                """,
                (args.ticker, args.ticker, args.form, args.form, args.section, args.section,
                 args.pattern),
            ).fetchall()  # fmt: skip
            shown = 0
            pattern = re.compile(args.pattern, re.I)
            for ticker, form, period, accession, item, text in rows:
                for match in pattern.finditer(text):
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    line_end = text.find("\n", match.end())
                    line_end = len(text) if line_end < 0 else line_end
                    print(f"## {ticker} {form} {period} {accession} {item} @{match.start()}")
                    print(text[line_start:line_end][:1500])
                    shown += 1
                    if shown >= args.max:
                        return
        else:
            found = conn.execute(
                "SELECT text FROM sections WHERE accession = %s AND item = %s",
                (args.accession, args.section),
            ).fetchone()
            print("NOT FOUND" if found is None else found[0][args.start : args.end])


if __name__ == "__main__":
    main()
