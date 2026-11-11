"""Which filings a question refers to, read from its wording.

"most recent 10-Q", "fiscal 2025 and fiscal 2026 10-Ks", "last two 10-Qs" and "the quarter
ended June 27, 2026" each name specific filings. Without this, a question about the latest
10-Q competes with near-identical passages from the same company's seven other filings.
Parsing is pure; resolution maps references to accessions for the question's tickers.

Scoping that picks the wrong filing is worse than none, so the parser only narrows on
explicit wording and resolution falls back to no scope when nothing matches.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import psycopg

from goldirham_filings.edgar import FORMS, Form
from goldirham_filings.parse import UNKNOWN_SECTION

FORM_PATTERN = re.compile(r"\b(10-[KQ]|20-F)s?\b(?!/A)|\b(annual|quarterly) reports?\b", re.I)
COUNT_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8}
LATEST = re.compile(
    r"\b(?:most recent|latest|last)\s+(?:(two|three|four|five|six|eight|\d{1,2})\s+)?"
    r"(?:fiscal (?:year )?\d{4}\s+)?$",
    re.I,
)
FISCAL_YEARS = re.compile(r"\bfiscal(?: year)? (\d{4})\b", re.I)
DATE = (
    r"\b(January|February|March|April|May|June|July|August|September|October|November|"
    r"December) (\d{1,2}), (\d{4})\b"
)
QUARTER_ENDED = re.compile(r"\b(?:quarter|period|year) ended\s+" + DATE, re.I)
PERIOD_END_TOLERANCE_DAYS = 7
# Section names a question can use, and the 10-K and 10-Q items they mean.
SECTION_INTENTS = (
    (re.compile(r"\brisk factors?\b", re.I), ("1A", "II-1A", "F-3")),
    (re.compile(r"\bMD&A\b|\bmanagement'?s discussion\b", re.I), ("7", "I-2", "F-5")),
    (re.compile(r"\blegal proceedings\b", re.I), ("3", "II-1")),
    (re.compile(r"\bquantitative and qualitative disclosures\b", re.I), ("7A", "I-3", "F-11")),
)


def section_intent(text: str) -> list[str]:
    """Sections the question names ('risk factors' is 1A in a 10-K and II-1A in a 10-Q).
    'unknown' is added so filings without Item headings, such as Intel's, stay searchable."""
    sections = [s for pattern, ids in SECTION_INTENTS if pattern.search(text) for s in ids]
    return [*sections, UNKNOWN_SECTION] if sections else []


@dataclass(frozen=True)
class FilingReference:
    """One mention of filings: a form (None for any) narrowed by recency, fiscal year or a
    period end date. A reference with no narrowing selects every filing of its form."""

    form: Form | None
    latest: int = 0
    fiscal_years: tuple[int, ...] = ()
    period_end: date | None = None

    @property
    def narrowed(self) -> bool:
        return bool(self.latest or self.fiscal_years or self.period_end)


def _date(month: str, day: str, year: str) -> date | None:
    """None for an impossible date such as June 31."""
    try:
        return datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date()
    except ValueError:
        return None


def _count(word: str | None) -> int:
    if not word:
        return 1
    return int(word) if word.isdigit() else COUNT_WORDS[word.lower()]


def parse_references(text: str) -> list[FilingReference]:
    refs: list[FilingReference] = []
    matches = list(FORM_PATTERN.finditer(text))
    for n, match in enumerate(matches):
        named = (match.group(1) or match.group(2)).upper()
        form: Form = (
            "20-F" if named == "20-F" else "10-K" if named in ("10-K", "ANNUAL") else "10-Q"
        )
        before = text[matches[n - 1].end() if n else 0 : match.start()]
        after = text[match.end() : matches[n + 1].start() if n + 1 < len(matches) else len(text)]
        latest = LATEST.search(before)
        ended = QUARTER_ENDED.match(after.lstrip(" ,").removeprefix("for the "))
        refs.append(
            FilingReference(
                form=form,
                latest=_count(latest.group(1)) if latest else 0,
                fiscal_years=tuple(int(y) for y in FISCAL_YEARS.findall(before)),
                period_end=_date(*ended.groups()) if ended else None,
            )
        )
    for match in QUARTER_ENDED.finditer(text):
        period_end = _date(*match.groups())
        if period_end is not None and not any(r.period_end == period_end for r in refs):
            refs.append(FilingReference(form=None, period_end=period_end))
    if not refs and re.search(r"\b(most recent|latest) (filings?|reports?)\b", text, re.I):
        refs.append(FilingReference(form=None, latest=1))
    # A bare "per its 10-Q" adds nothing when another reference already narrows that form.
    return [
        r
        for r in refs
        if r.narrowed or not any(o.narrowed and o.form in (None, r.form) for o in refs)
    ]


@dataclass(frozen=True)
class _Filing:
    ticker: str
    form: str
    accession: str
    period_end: date | None
    fiscal_year: int | None


def _matches(filing: _Filing, ref: FilingReference) -> bool:
    if ref.fiscal_years:
        year = filing.fiscal_year or (filing.period_end.year if filing.period_end else None)
        if year not in ref.fiscal_years:
            return False
    if ref.period_end:
        if filing.period_end is None:
            return False
        if abs((filing.period_end - ref.period_end).days) > PERIOD_END_TOLERANCE_DAYS:
            return False
    return True


def resolve_accessions(
    conn: psycopg.Connection[tuple[Any, ...]],
    refs: list[FilingReference],
    tickers: list[str] | None,
    forms: list[Form] | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
) -> list[str]:
    """Accessions matching any reference, per ticker (all tickers when None), within the
    caller's own form and period filters. Empty when nothing matches."""
    rows = conn.execute(
        """
        SELECT co.ticker, f.form, f.accession, f.period_end, f.fiscal_year FROM filings f
        JOIN companies co ON co.cik = f.cik
        WHERE (%s::text[] IS NULL OR co.ticker = ANY(%s))
          AND (%s::text[] IS NULL OR f.form = ANY(%s))
          AND (%s::date IS NULL OR f.period_end >= %s)
          AND (%s::date IS NULL OR f.period_end <= %s)
        ORDER BY f.period_end DESC NULLS LAST, f.filed_at DESC
        """,
        [
            *[[t.upper() for t in tickers] if tickers else None] * 2,
            *[forms or None] * 2,
            *[period_from] * 2,
            *[period_to] * 2,
        ],
    ).fetchall()
    filings = [_Filing(*row) for row in rows]
    selected: set[str] = set()
    for ref in refs:
        for ticker in {f.ticker for f in filings}:
            for form in (ref.form,) if ref.form else FORMS:
                candidates = [
                    f for f in filings if f.ticker == ticker and f.form == form and _matches(f, ref)
                ]
                if ref.latest:
                    candidates = candidates[: ref.latest]
                selected.update(f.accession for f in candidates)
    return sorted(selected)
