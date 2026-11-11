"""20-F filers: the form flows through parsing, scoping, checks, facts and the tool schemas.

The filing below is synthetic; no real 20-F has been parsed yet.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from mcp import Client

from goldirham_filings import ingest
from goldirham_filings.answer import Answerer
from goldirham_filings.companies import BY_TICKER
from goldirham_filings.doctor import tickers_check
from goldirham_filings.edgar import EdgarClient, FilingRef, Form, parse_submission_page
from goldirham_filings.embed import OllamaEmbedder
from goldirham_filings.parse import (
    html_to_text,
    missing_required,
    section_for_form,
    split_sections,
)
from goldirham_filings.retrieve import Filters, Passage, QuestionPart
from goldirham_filings.scope import FilingReference, parse_references, section_intent
from goldirham_filings.server import build_server
from goldirham_filings.xbrl import lookup, parse_company_facts

FILLER = "Filler sentence. " * 70
TWENTY_F = f"""<html><body>
<p>Item 1. Identity of Directors</p><p>Not applicable.</p>
<p>Item 3. Key Information</p><p>Export controls could harm our business. {FILLER}</p>
<p>Item 5. Operating and Financial Review and Prospects</p><p>Revenue grew. {FILLER}</p>
<p>Item 16K. Cybersecurity</p><p>We run a security program. {FILLER}</p>
</body></html>"""


def test_a_20f_splits_into_its_own_prefixed_items() -> None:
    sections = split_sections(html_to_text(TWENTY_F), "20-F")
    by_item = {s.item: s for s in sections}
    assert {"F-3", "F-5", "F-16K"} <= set(by_item)
    assert by_item["F-3"].title == "Key Information"
    assert "Export controls" in by_item["F-3"].text
    assert missing_required(sections, "20-F") == []


def test_20f_items_do_not_collide_with_10k_items() -> None:
    assert section_for_form("F-3", "20-F") == "F-3"
    assert section_for_form("3", "20-F") is None
    assert section_for_form("F-3", "10-K") is None


def test_item_letters_beyond_c_stay_ignored_for_10k() -> None:
    text = html_to_text(TWENTY_F.replace("Item 5.", "Item 7.").replace("Item 3.", "Item 1A."))
    assert not any(s.item.endswith("16K") for s in split_sections(text, "10-K"))


def test_submission_page_keeps_20f() -> None:
    page = {
        "form": ["20-F", "6-K"],
        "accessionNumber": ["0001046179-26-000001", "0001046179-26-000002"],
        "filingDate": ["2026-04-16", "2026-04-20"],
        "reportDate": ["2025-12-31", ""],
        "primaryDocument": ["a.htm", "b.htm"],
    }
    assert [r.form for r in parse_submission_page(1046179, page)] == ["20-F"]


def test_a_question_can_name_a_20f() -> None:
    assert parse_references("What does TSMC's most recent 20-F say?") == [
        FilingReference(form="20-F", latest=1)
    ]


def test_risk_factor_questions_include_the_20f_item() -> None:
    assert "F-3" in section_intent("Does ASML list export controls as a risk factor?")
    assert "F-5" in section_intent("What does the MD&A say about supply?")


class Searcher:
    def __init__(self, text: str) -> None:
        self.passage = Passage(
            chunk_id=1, ticker="TSM", company="TSMC", form="20-F", accession="A",
            period_end=date(2025, 12, 31), filed_at=date(2026, 4, 16), section="F-5",
            section_title="Operating and Financial Review", start_char=0, end_char=len(text),
            text=text, source_url="u", similarity=0.8,
        )  # fmt: skip

    def search(self, query: str, filters: Filters | None = None, k: int = 8) -> list[Passage]:
        return [self.passage]

    def parts(self, query: str, filters: Filters | None = None) -> list[QuestionPart]:
        return []


class Chat:
    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        sentence = {"text": "Per its Form 20-F, revenue grew.", "citations": [1]}
        return json.dumps({"answerable": True, "sentences": [sentence]})


def test_the_form_name_is_not_a_quantity() -> None:
    result = Answerer(Searcher("Revenue grew."), Chat()).answer("q")
    assert result.status == "answered"
    assert result.sentences[0].unsupported_numbers == []


def test_a_fiscal_year_resolves_through_the_20f() -> None:
    data = {"facts": {"us-gaap": {"Revenues": {"label": "Revenues", "units": {"USD": [
        {"start": "2025-01-01", "end": "2025-12-31", "val": 100, "accn": "A", "fy": 2025,
         "fp": "FY", "form": "20-F", "filed": "2026-04-16"},
    ]}}}}}  # fmt: skip
    found = lookup(parse_company_facts(1, data), "TSM", "Revenues", 2025, "FY")
    assert [f.value for f in found.facts] == [100.0]


@pytest.mark.anyio
async def test_the_tool_schemas_accept_20f() -> None:
    embedder = OllamaEmbedder(
        "nomic-embed-text", "http://x", transport=httpx.MockTransport(_refuse)
    )
    server = build_server(_no_database, embedder, Chat())
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert "20-F" in str(tools["search_filings"].input_schema)
    assert "20-F" in str(tools["compare_filings"].input_schema)


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def _no_database() -> Any:
    raise AssertionError("not reached")


REF = FilingRef("TEST-0000000020", 1046179, "20-F", date(2026, 4, 16), date(2025, 12, 31), "t.htm")


class ForeignEdgar(EdgarClient):
    def __init__(self) -> None:
        self.asked: list[int] = []

    def filings(self, cik: int, limits: dict[Form, int]) -> list[FilingRef]:
        self.asked.append(cik)
        return [REF]

    def get(self, url: str) -> httpx.Response:
        return httpx.Response(200, content=TWENTY_F.encode(), request=httpx.Request("GET", url))


@pytest.mark.db
@pytest.mark.parametrize(("include", "asked"), [(False, []), (True, [1046179])])
def test_foreign_filers_are_indexed_only_when_asked(
    include: bool,
    asked: list[int],
    fixture_conn: psycopg.Connection[tuple[Any, ...]],
    tmp_path: Path,
) -> None:
    edgar = ForeignEdgar()
    try:
        report = ingest.ingest(
            fixture_conn, edgar, tmp_path, [BY_TICKER["TSM"]], {"20-F": 1}, include_foreign=include
        )
        assert edgar.asked == asked
        assert [r.ref.form for r in report.results] == (["20-F"] if include else [])
    finally:
        fixture_conn.rollback()
        fixture_conn.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
        fixture_conn.execute("DELETE FROM index_runs")
        fixture_conn.commit()


def test_doctor_accepts_20f_tickers_only_when_enabled() -> None:
    assert not tickers_check(["TSM"]).ok
    assert tickers_check(["TSM"], allow_foreign=True).ok
