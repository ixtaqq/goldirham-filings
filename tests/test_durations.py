from datetime import date
from decimal import Decimal

from goldirham_filings.answer import unsupported_durations
from goldirham_filings.xbrl import Fact

NINE_MONTHS = (date(2025, 10, 27), date(2026, 7, 26))
THREE_MONTHS = (date(2026, 4, 27), date(2026, 7, 26))


def fact(value: int, period: tuple[date | None, date]) -> Fact:
    return Fact(
        cik=1, taxonomy="us-gaap", concept="Capex", label="Capex", unit="USD",
        period_start=period[0], period_end=period[1], value=Decimal(value), accession="A",
        form="10-Q", fiscal_year=2027, fiscal_period="Q3", filed=date(2026, 8, 26),
    )  # fmt: skip


def test_a_nine_month_figure_quoted_as_the_quarters_is_flagged() -> None:
    # heldout-lookup-009: nine-month capital expenditures reported as the quarter's.
    facts = [fact(9_000_000_000, NINE_MONTHS)]
    sentence = "Capital expenditures for the quarter were $9,000 million."
    assert unsupported_durations(sentence, facts) == ["$9,000 million"]


def test_a_figure_reported_for_the_claimed_duration_passes() -> None:
    facts = [fact(3_100_000_000, THREE_MONTHS)]
    assert unsupported_durations("Capex for the quarter was $3,100 million.", facts) == []


def test_a_figure_reported_for_both_durations_passes() -> None:
    facts = [fact(9_000_000_000, NINE_MONTHS), fact(9_000_000_000, THREE_MONTHS)]
    assert unsupported_durations("Capex in the quarter was $9,000 million.", facts) == []


def test_the_claim_is_read_for_each_duration() -> None:
    facts = [fact(3_100_000_000, THREE_MONTHS)]
    sentence = "For the nine months ended July 26, 2026, capex was $3,100 million."
    assert unsupported_durations(sentence, facts) == ["$3,100 million"]


def test_rounded_amounts_match_at_their_stated_precision() -> None:
    facts = [fact(89_012_000_000, NINE_MONTHS)]
    assert unsupported_durations("Quarter revenue was $89.0 billion.", facts) == ["$89.0 billion"]
    assert unsupported_durations("Quarter revenue was $88.0 billion.", facts) == []


def test_no_claim_or_no_matching_fact_flags_nothing() -> None:
    facts = [fact(9_000_000_000, NINE_MONTHS)]
    assert unsupported_durations("Capex was $9,000 million.", facts) == []
    assert unsupported_durations("Capex for the quarter was $1,234 million.", facts) == []


def test_a_balance_at_a_date_has_no_duration_to_contradict() -> None:
    facts = [fact(9_000_000_000, (None, date(2026, 7, 26)))]
    assert unsupported_durations("Cash at quarter end was $9,000 million.", facts) == []
