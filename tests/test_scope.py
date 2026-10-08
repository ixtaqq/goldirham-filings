from datetime import date
from typing import Any

import psycopg
import pytest

from goldirham_filings.scope import FilingReference, parse_references, resolve_accessions


def test_most_recent_form() -> None:
    assert parse_references("Does NVIDIA's most recent 10-Q mention SB Energy?") == [
        FilingReference(form="10-Q", latest=1)
    ]


def test_fiscal_year_and_latest_in_one_comparison() -> None:
    question = "How did it change between its fiscal 2025 10-K and its most recent 10-Q?"
    assert parse_references(question) == [
        FilingReference(form="10-K", fiscal_years=(2025,)),
        FilingReference(form="10-Q", latest=1),
    ]


def test_two_fiscal_years_on_a_plural_form() -> None:
    question = "How did the risk change between its fiscal 2025 and fiscal 2026 10-Ks?"
    assert parse_references(question) == [FilingReference(form="10-K", fiscal_years=(2025, 2026))]


def test_last_two_quarterly_reports() -> None:
    assert parse_references("Compare Q2 with Q1 based on Intel's last two 10-Qs.") == [
        FilingReference(form="10-Q", latest=2)
    ]


def test_quarter_ended_date_after_the_form() -> None:
    question = "In Microsoft's 10-Q for the quarter ended March 31, 2026, what grew?"
    assert parse_references(question) == [
        FilingReference(form="10-Q", period_end=date(2026, 3, 31))
    ]


def test_bare_form_is_dropped_when_a_date_narrows_the_question() -> None:
    question = "What share came from abroad in the quarter ended July 26, 2026, per its 10-Q?"
    assert parse_references(question) == [FilingReference(form=None, period_end=date(2026, 7, 26))]


def test_latest_filings_without_a_form() -> None:
    assert parse_references("How do they describe OpenAI in their latest filings?") == [
        FilingReference(form=None, latest=1)
    ]


def test_question_without_a_filing_reference() -> None:
    assert parse_references("Which companies mention Singapore's global minimum tax?") == []


def test_annual_report_in_any_case_is_a_10k() -> None:
    assert parse_references("What does the Annual Report say about export controls?") == [
        FilingReference(form="10-K")
    ]


def test_bare_form_of_a_different_form_is_kept() -> None:
    question = "Compare risk factors in the 10-K with the most recent 10-Q."
    assert parse_references(question) == [
        FilingReference(form="10-K"),
        FilingReference(form="10-Q", latest=1),
    ]


def test_all_fiscal_years_before_the_form_count() -> None:
    question = "How did fiscal 2025 revenue compare with fiscal 2024 in the 10-K?"
    assert parse_references(question) == [FilingReference(form="10-K", fiscal_years=(2025, 2024))]


def test_counts_and_last_year() -> None:
    assert parse_references("Summarize the last four 10-Qs.") == [
        FilingReference(form="10-Q", latest=4)
    ]
    assert parse_references("What changed in last year's 10-K?") == [FilingReference(form="10-K")]


def test_latest_with_fiscal_year() -> None:
    assert parse_references("What does the latest fiscal 2025 10-K say?") == [
        FilingReference(form="10-K", latest=1, fiscal_years=(2025,))
    ]


def test_amendments_are_not_references() -> None:
    assert parse_references("What did the most recent 10-K/A amend?") == []


MSFT_FY2025_10K = "0000950170-25-100235"
MSFT_FY2026_10K = "0001193125-26-323660"
NVDA_FY2027_Q2_10Q = "0001045810-26-000075"


@pytest.mark.db
@pytest.mark.parametrize(
    ("question", "tickers", "expected"),
    [
        ("the latest fiscal 2025 10-K", ["MSFT"], [MSFT_FY2025_10K]),
        ("its fiscal 2026 10-K", ["MSFT"], [MSFT_FY2026_10K]),
        ("its last two 10-Ks", ["MSFT"], [MSFT_FY2025_10K, MSFT_FY2026_10K]),
        ("NVIDIA's fiscal 2027 10-Q", ["NVDA"], [NVDA_FY2027_Q2_10Q]),
        ("NVIDIA's fiscal 2026 10-Q", ["NVDA"], []),
        ("the 10-Q for the quarter ended July 25, 2026", ["NVDA"], [NVDA_FY2027_Q2_10Q]),
    ],
)
def test_resolve_uses_each_filings_own_fiscal_year(
    fixture_conn: psycopg.Connection[tuple[Any, ...]],
    question: str,
    tickers: list[str],
    expected: list[str],
) -> None:
    assert resolve_accessions(fixture_conn, parse_references(question), tickers) == expected


@pytest.mark.db
def test_resolve_respects_the_callers_period_filter(
    fixture_conn: psycopg.Connection[tuple[Any, ...]],
) -> None:
    refs = parse_references("its most recent 10-K")
    assert resolve_accessions(fixture_conn, refs, ["MSFT"], period_to=date(2025, 12, 31)) == [
        MSFT_FY2025_10K
    ]
