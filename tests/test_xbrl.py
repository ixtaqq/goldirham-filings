from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from goldirham_filings.xbrl import lookup, parse_company_facts

K_FY26 = "0001045810-26-000021"
Q1_FY27 = "0001045810-26-000052"
Q2_FY27 = "0001045810-26-000075"


def row(start: str | None, end: str, val: int, accn: str, fy: int, fp: str, form: str,
        filed: str) -> dict[str, Any]:  # fmt: skip
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": fy, "fp": fp,
            "form": form, "filed": filed}  # fmt: skip


# Shaped like NVIDIA's companyfacts: comparatives repeat across filings, 10-Qs carry quarter
# and year-to-date durations, and a proxy statement repeats a 10-K figure.
DATA = {"facts": {"us-gaap": {
    "Revenues": {"label": "Revenues", "units": {"USD": [
        row("2025-01-27", "2026-01-25", 215938, K_FY26, 2026, "FY", "10-K", "2026-02-25"),
        row("2025-01-27", "2026-01-25", 215938, "def14a", 2026, "FY", "DEF 14A", "2026-05-12"),
        row("2026-01-26", "2026-04-26", 81615, Q1_FY27, 2027, "Q1", "10-Q", "2026-05-20"),
        row("2026-04-27", "2026-07-26", 96221, Q2_FY27, 2027, "Q2", "10-Q", "2026-08-26"),
        row("2026-01-26", "2026-07-26", 177837, Q2_FY27, 2027, "Q2", "10-Q", "2026-08-26"),
    ]}},
    "CashAndCashEquivalentsAtCarryingValue": {"label": "Cash", "units": {"USD": [
        row(None, "2026-01-25", 10605, K_FY26, 2026, "FY", "10-K", "2026-02-25"),
        row(None, "2026-01-25", 10605, Q2_FY27, 2027, "Q2", "10-Q", "2026-08-26"),
        row(None, "2026-07-26", 22443, Q2_FY27, 2027, "Q2", "10-Q", "2026-08-26"),
    ]}},
}}}  # fmt: skip
FACTS = parse_company_facts(1045810, DATA)


def test_only_10k_and_10q_facts_are_kept() -> None:
    assert len(FACTS) == 7
    assert {f.form for f in FACTS} == {"10-K", "10-Q"}


def test_fiscal_year_resolves_through_the_filing_that_covers_it() -> None:
    found = lookup(FACTS, "NVDA", "revenue", fiscal_year=2026, fiscal_period="FY")
    assert found.concept == "Revenues" and found.period_end == date(2026, 1, 25)
    [fact] = found.facts
    assert (fact.value, fact.accession, fact.duration_days) == (Decimal(215938), K_FY26, 364)
    assert fact.source_url == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/"


def test_a_quarter_returns_quarter_and_year_to_date_with_a_note() -> None:
    found = lookup(FACTS, "NVDA", "Revenues", fiscal_year=2027, fiscal_period="Q2")
    assert [(f.value, f.duration_days) for f in found.facts] == [
        (Decimal(96221), 91), (Decimal(177837), 182)
    ]  # fmt: skip
    assert "duration_days" in found.notes[0]


def test_an_instant_comes_from_the_filing_whose_own_period_it_is() -> None:
    found = lookup(FACTS, "NVDA", "cash and cash equivalents", period_end=date(2026, 1, 27))
    assert [(f.value, f.accession) for f in found.facts] == [(Decimal(10605), K_FY26)]
    latest = lookup(FACTS, "NVDA", "CashAndCashEquivalentsAtCarryingValue")
    assert latest.period_end == date(2026, 7, 26) and latest.facts[0].value == Decimal(22443)


def test_unknown_concepts_and_unreported_periods_say_so() -> None:
    with pytest.raises(ValueError, match="no XBRL concept matching 'cash flow'"):
        lookup(FACTS, "NVDA", "cash flow")
    with pytest.raises(ValueError, match="Close matches: CashAndCashEquivalentsAtCarryingValue"):
        lookup(FACTS, "NVDA", "cash")
    with pytest.raises(ValueError, match="no Q3 2027 10-K or 10-Q"):
        lookup(FACTS, "NVDA", "Revenues", fiscal_year=2027, fiscal_period="Q3")
    missing = lookup(FACTS, "NVDA", "Revenues", period_end=date(2025, 10, 26))
    assert missing.facts == [] and "not reported for a period ending 2025-10-26" in missing.notes[0]


def test_cover_page_dates_and_retired_tags_do_not_mislead() -> None:
    q3 = "0000002488-26-000191"
    data = {"facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"label": "Shares", "units": {"shares": [
            row(None, "2026-10-28", 1620, q3, 2026, "Q3", "10-Q", "2026-11-04")]}}},
        "us-gaap": {
            "Revenues": {"label": "Revenues", "units": {"USD": [
                row("2017-07-02", "2017-09-30", 1643, "old", 2017, "Q3", "10-Q", "2017-11-03")]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"label": "Revenue", "units": {
                "USD": [row("2026-06-28", "2026-09-26", 13377, q3, 2026, "Q3", "10-Q",
                            "2026-11-04"),
                        row("2025-12-28", "2026-09-26", 35166, q3, 2026, "Q3", "10-Q",
                            "2026-11-04")]}},
        },
    }}  # fmt: skip
    found = lookup(parse_company_facts(2488, data), "AMD", "revenue", 2026, "Q3")
    assert found.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert found.period_end == date(2026, 9, 26)
    assert [f.value for f in found.facts] == [Decimal(13377), Decimal(35166)]
