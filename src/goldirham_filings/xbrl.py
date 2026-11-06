"""Exact reported figures from EDGAR XBRL companyfacts.

companyfacts lists every fact a company tagged, in every filing that repeated it. A fact's
`fy` and `fp` describe the filing that reported it, not the fact's own period: the 10-K for
fiscal 2026 repeats fiscal 2025 figures with fy=2026. So a fiscal period is resolved through
the filing that covers it (its own period end is the latest period end it reports), and the
facts ending on that date are returned with their durations. Nothing is computed: a fourth
quarter that no filing reports on its own is not derived from the year and nine months.

Parsing and selection are pure; loading and querying stay at the edges.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import psycopg
from pydantic import BaseModel

from goldirham_filings.edgar import FORMS, EdgarClient

TAXONOMIES = ("us-gaap", "dei")
PERIOD_END_TOLERANCE_DAYS = 7
FiscalPeriod = Literal["FY", "Q1", "Q2", "Q3"]
# Plain names for the concepts questions ask about most; the first one a company uses wins.
ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"
    ),
    "net income": ("NetIncomeLoss",),
    "gross profit": ("GrossProfit",),
    "operating income": ("OperatingIncomeLoss",),
    "research and development": ("ResearchAndDevelopmentExpense",),
    "diluted eps": ("EarningsPerShareDiluted",),
    "cash and cash equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "total assets": ("Assets",),
    "capital expenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "operating cash flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "long-term debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "share repurchases": ("PaymentsForRepurchaseOfCommonStock",),
}  # fmt: skip


@dataclass(frozen=True)
class Fact:
    cik: int
    taxonomy: str
    concept: str
    label: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    accession: str
    form: str
    fiscal_year: int | None
    fiscal_period: str | None
    filed: date


def parse_company_facts(cik: int, data: dict[str, Any]) -> list[Fact]:
    """Facts from 10-K and 10-Q filings in the given taxonomies; amendments are skipped."""
    facts = []
    for taxonomy in TAXONOMIES:
        for concept, body in data.get("facts", {}).get(taxonomy, {}).items():
            for unit, rows in body.get("units", {}).items():
                for row in rows:
                    if row.get("form") not in FORMS:
                        continue
                    facts.append(Fact(
                        cik=cik, taxonomy=taxonomy, concept=concept,
                        label=body.get("label") or concept, unit=unit,
                        period_start=date.fromisoformat(row["start"]) if row.get("start") else None,
                        period_end=date.fromisoformat(row["end"]), value=Decimal(str(row["val"])),
                        accession=row["accn"], form=row["form"], fiscal_year=row.get("fy"),
                        fiscal_period=row.get("fp"), filed=date.fromisoformat(row["filed"]),
                    ))  # fmt: skip
    return facts


class FactValue(BaseModel):
    concept: str
    label: str
    value: Decimal
    unit: str
    period_start: date | None
    period_end: date
    duration_days: int | None
    accession: str
    form: str
    filed: date
    source_url: str


class FactLookup(BaseModel):
    ticker: str
    concept: str
    period_end: date
    facts: list[FactValue]
    notes: list[str]


def report_period_ends(facts: Iterable[Fact]) -> dict[str, date]:
    """Each filing's own period end: the date it reports the most facts for. Not the latest
    date: cover-page facts such as shares outstanding are dated after the period."""
    counts: dict[str, Counter[date]] = defaultdict(Counter)
    for f in facts:
        counts[f.accession][f.period_end] += 1
    return {accession: c.most_common(1)[0][0] for accession, c in counts.items()}


def resolve_concept(query: str, latest: dict[str, date]) -> str | None:
    """An exact concept name (any case), or for a plain alias the candidate the company
    reported most recently: companies switch tags (Revenues to RevenueFromContract...)."""
    lowered = {c.lower(): c for c in latest}
    if query.lower() in lowered:
        return lowered[query.lower()]
    candidates = [c for c in ALIASES.get(query.lower().strip(), ()) if c in latest]
    return max(candidates, key=lambda c: latest[c], default=None)


def suggest_concepts(query: str, labels: dict[str, str], limit: int = 8) -> list[str]:
    """Concepts whose name or label contains every word of the query."""
    words = query.lower().split()
    found = [c for c, label in sorted(labels.items())
             if all(w in f"{c} {label}".lower() for w in words)]  # fmt: skip
    return found[:limit]


def target_period_end(
    facts: list[Fact],
    fiscal_year: int | None,
    fiscal_period: FiscalPeriod | None,
    period_end: date | None,
) -> date | None:
    """The period end the caller means: a fiscal period's filing end, an explicit date, or the
    latest period this company has reported."""
    ends = report_period_ends(facts)
    if fiscal_year is not None and fiscal_period is not None:
        form = "10-K" if fiscal_period == "FY" else "10-Q"
        matches = sorted({ends[f.accession] for f in facts
                          if f.fiscal_year == fiscal_year and f.fiscal_period == fiscal_period
                          and f.form == form})  # fmt: skip
        return matches[-1] if matches else None
    if period_end is not None:
        return period_end
    return max(ends.values(), default=None)


def select_facts(facts: list[Fact], concept: str, end: date, ends: dict[str, date]) -> list[Fact]:
    """One fact per duration ending at `end` (within a week), from the filing whose own
    period it is (`ends`: each filing's own period end, from all its facts) when that filing
    reports it, else the most recently filed copy."""
    matching = [f for f in facts if f.concept == concept
                and abs((f.period_end - end).days) <= PERIOD_END_TOLERANCE_DAYS]  # fmt: skip
    chosen: dict[tuple[date | None, date, str], Fact] = {}
    for f in matching:
        key = (f.period_start, f.period_end, f.unit)
        own = ends[f.accession] == f.period_end
        current = chosen.get(key)
        current_own = current is not None and ends[current.accession] == current.period_end
        if (
            current is None
            or (own and not current_own)
            or (own == current_own and f.filed > current.filed)
        ):
            chosen[key] = f
    return sorted(chosen.values(), key=lambda f: f.period_start or f.period_end, reverse=True)


def filing_index_url(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"


def to_value(f: Fact) -> FactValue:
    return FactValue(
        concept=f.concept, label=f.label, value=f.value, unit=f.unit,
        period_start=f.period_start, period_end=f.period_end,
        duration_days=(f.period_end - f.period_start).days + 1 if f.period_start else None,
        accession=f.accession, form=f.form, filed=f.filed,
        source_url=filing_index_url(f.cik, f.accession),
    )  # fmt: skip


def fetch_company_facts(client: EdgarClient, cik: int) -> dict[str, Any]:
    return client.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")


def store_facts(conn: psycopg.Connection[tuple[Any, ...]], cik: int, facts: list[Fact]) -> int:
    """Replace one company's facts in a single transaction."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM xbrl_facts WHERE cik = %s", (cik,))
        cur.executemany(
            """
            INSERT INTO xbrl_facts (cik, taxonomy, concept, label, unit, period_start,
                                    period_end, value, accession, form, fiscal_year,
                                    fiscal_period, filed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(f.cik, f.taxonomy, f.concept, f.label, f.unit, f.period_start, f.period_end,
              f.value, f.accession, f.form, f.fiscal_year, f.fiscal_period, f.filed)
             for f in facts],
        )  # fmt: skip
    conn.commit()
    return len(facts)


def company_facts(conn: psycopg.Connection[tuple[Any, ...]], cik: int) -> list[Fact]:
    rows = conn.execute(
        """
        SELECT cik, taxonomy, concept, label, unit, period_start, period_end, value, accession,
               form, fiscal_year, fiscal_period, filed
        FROM xbrl_facts WHERE cik = %s
        """,
        (cik,),
    ).fetchall()
    return [Fact(*r) for r in rows]


def lookup(
    facts: list[Fact],
    ticker: str,
    concept_query: str,
    fiscal_year: int | None = None,
    fiscal_period: FiscalPeriod | None = None,
    period_end: date | None = None,
) -> FactLookup:
    """Pure: the reported values of one concept for one period. Raises ValueError with
    suggestions when the concept or period is not reported."""
    labels = {f.concept: f.label for f in facts}
    latest: dict[str, date] = {}
    for f in facts:
        latest[f.concept] = max(latest.get(f.concept, f.period_end), f.period_end)
    concept = resolve_concept(concept_query, latest)
    if concept is None:
        hints = suggest_concepts(concept_query, labels)
        aliases = ", ".join(sorted(ALIASES))
        raise ValueError(
            f"{ticker} reports no XBRL concept matching {concept_query!r}."
            + (f" Close matches: {', '.join(hints)}." if hints else "")
            + f" Plain names accepted: {aliases}."
        )
    concept_facts = [f for f in facts if f.concept == concept]
    end = target_period_end(facts, fiscal_year, fiscal_period, period_end)
    if end is None:
        raise ValueError(
            f"{ticker} has no {fiscal_period} {fiscal_year} 10-K or 10-Q in its XBRL facts."
            if fiscal_year is not None
            else f"{ticker} has no XBRL facts."
        )
    chosen = select_facts(concept_facts, concept, end, report_period_ends(facts))
    notes = []
    if not chosen:
        last = max((f.period_end for f in concept_facts), default=None)
        latest_note = f"; the latest reported period ends {last}" if last else ""
        notes.append(f"{concept} is not reported for a period ending {end}{latest_note}.")
    if len({(f.period_start, f.period_end) for f in chosen}) > 1:
        notes.append("Several durations end on this date (quarter, year to date, year); "
                     "duration_days tells them apart.")  # fmt: skip
    return FactLookup(ticker=ticker, concept=concept, period_end=end,
                      facts=[to_value(f) for f in chosen], notes=notes)  # fmt: skip
