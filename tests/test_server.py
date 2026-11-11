"""MCP tools end to end over the fixture corpus, through the in-process MCP client."""

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import Client

from goldirham_filings.db import connect
from goldirham_filings.fixtures import FixtureEmbedder
from goldirham_filings.server import build_server

NVDA_Q = (
    "What did NVIDIA report as Data Center revenue in its most recent 10-Q (quarter ended "
    "July 26, 2026), and how did it change?"
)
NVDA_10Q = "0001045810-26-000075"
NVDA_QUOTE = (
    "Data Center revenue was $89.0 billion, up 117% from a year ago and up 18% sequentially, "
    "driven by the ramp of our Blackwell Ultra infrastructure."
)

pytestmark = [pytest.mark.db, pytest.mark.anyio]


class CitingChat:
    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        return json.dumps({"answerable": True, "sentences": [
            {"text": "NVIDIA reported its Data Center revenue.", "citations": [1]}]})  # fmt: skip


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    server = build_server(lambda: connect(url, read_only=True), FixtureEmbedder(), CitingChat())
    async with Client(server) as c:
        yield c


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content
    return content


async def test_all_tools_are_listed_read_only(client: Client) -> None:
    tools = (await client.list_tools()).tools
    assert sorted(t.name for t in tools) == [
        "answer_question", "compare_filings", "get_financial_fact", "get_passage", "list_coverage",
        "search_filings",
    ]  # fmt: skip
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


async def test_list_coverage_reports_fixture_companies_and_foreign_filers(client: Client) -> None:
    coverage = await call(client, "list_coverage")
    assert {c["ticker"]: c["forms"] for c in coverage["companies"]} == {
        "AMAT": {"10-K": 1, "10-Q": 1},
        "AMD": {"10-K": 1, "10-Q": 1},
        "MSFT": {"10-K": 2, "10-Q": 1},
        "MU": {"10-K": 1, "10-Q": 1},
        "NVDA": {"10-K": 1, "10-Q": 1},
    }
    assert (
        coverage["not_covered"][0]
        == "TSM, ASML, ARM: foreign private issuers (20-F/6-K), not indexed"
    )


async def test_search_is_scoped_to_the_named_filing_and_finds_the_sentence(client: Client) -> None:
    # The question names its filing, so scoping limits results to that 10-Q. Within it the gold
    # sentence ranked 8th when measured; the CI gate tracks ranking quality, not this test.
    result = await call(client, "search_filings", query=NVDA_Q, tickers=["NVDA"], k=10)
    passages = result["passages"]
    assert {p["accession"] for p in passages} == {NVDA_10Q}
    assert any(NVDA_QUOTE in p["text"] for p in passages)


async def test_get_passage_returns_the_exact_span_of_the_named_section(client: Client) -> None:
    start = 17134
    found = await call(client, "get_passage", accession=NVDA_10Q, section="I-2",
                       start_char=start, end_char=start + len(NVDA_QUOTE))  # fmt: skip
    assert found["passage"]["text"] == NVDA_QUOTE
    assert found["passage"]["section"] == "I-2"
    other = await call(client, "get_passage", accession=NVDA_10Q, section="I-1",
                       start_char=start, end_char=start + len(NVDA_QUOTE))  # fmt: skip
    assert other["passage"]["section"] == "I-1"
    assert other["passage"]["text"] != NVDA_QUOTE


async def test_get_passage_rejects_offsets_without_section(client: Client) -> None:
    result = await client.call_tool("get_passage", {"accession": NVDA_10Q, "start_char": 5})
    assert result.is_error


async def test_answer_question_returns_cited_sentences_with_their_passages(client: Client) -> None:
    answer = await call(client, "answer_question", question=NVDA_Q, tickers=["NVDA"])
    assert answer["status"] == "answered"
    assert [s["citations"] for s in answer["sentences"]] == [[1]]
    assert answer["passages"][0]["accession"] == NVDA_10Q
    assert answer["disclaimer"] == "Summarizes SEC filings in the index. Not investment advice."


async def test_compare_filings_orders_the_two_microsoft_10ks(client: Client) -> None:
    result = await call(client, "compare_filings", ticker="MSFT", section="1A", k=3)
    assert (result["older"]["period_end"], result["newer"]["period_end"]) == (
        "2025-06-30", "2026-06-30",
    )  # fmt: skip
    assert len(result["changed"]) == 3
    assert all(c["newer"]["accession"] == result["newer"]["accession"] for c in result["changed"])
    similarities = [c["similarity"] for c in result["changed"]]
    assert similarities == sorted(similarities)

    swapped = await call(client, "compare_filings", ticker="MSFT", section="1A", k=1,
                         older_accession=result["newer"]["accession"],
                         newer_accession=result["older"]["accession"])  # fmt: skip
    assert swapped["newer"]["period_end"] == "2026-06-30"


async def test_question_about_an_unindexed_period_abstains(client: Client) -> None:
    answer = await call(client, "answer_question", tickers=["NVDA"],
                        question="What did NVIDIA report in its 10-Q for the quarter ended "
                        "October 25, 2026?")  # fmt: skip
    assert (answer["status"], answer["notes"]) == (
        "abstained", ["no indexed filing matches the question"],
    )  # fmt: skip


class UnembeddedModel(FixtureEmbedder):
    """The fixture's vectors under a model name that has none stored."""

    def __init__(self) -> None:
        super().__init__()
        self.model = "mxbai-embed-large"


async def test_search_without_vectors_for_the_model_says_it_is_keyword_only() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    server = build_server(lambda: connect(url), UnembeddedModel(), CitingChat())
    async with Client(server) as c:
        result = await call(c, "search_filings", query=NVDA_Q, tickers=["NVDA"], k=5)
        answer = await call(c, "answer_question", question=NVDA_Q, tickers=["NVDA"])
    assert result["passages"], "keyword search still finds passages"
    assert all(p["similarity"] is None for p in result["passages"])
    assert result["notes"] == [
        "No chunk has a mxbai-embed-large vector, so search is keyword-only and similarity "
        "scores are missing. Run: filings embed"
    ]
    assert answer["notes"][-1] == result["notes"][0]


async def test_compare_filings_matches_sections_across_a_10k_and_a_10q(client: Client) -> None:
    result = await call(client, "compare_filings", ticker="NVDA", section="1A", k=3,
                        older_accession="0001045810-26-000021",
                        newer_accession=NVDA_10Q)  # fmt: skip
    assert (result["older_section"], result["newer_section"]) == ("1A", "II-1A")
    assert len(result["changed"]) == 3
    assert all(c["newer"]["section"] == "II-1A" for c in result["changed"])
    assert all(c["closest_older"]["section"] == "1A" for c in result["changed"])
    focused = await call(client, "compare_filings", ticker="NVDA", section="7", k=2,
                         focus=NVDA_Q, older_accession="0001045810-26-000021",
                         newer_accession=NVDA_10Q)  # fmt: skip
    assert {p["section"] for p in focused["older_passages"]} == {"7"}
    assert {p["section"] for p in focused["newer_passages"]} == {"I-2"}
    unmatched = await client.call_tool("compare_filings", {
        "ticker": "NVDA", "section": "1C", "older_accession": "0001045810-26-000021",
        "newer_accession": NVDA_10Q})  # fmt: skip
    assert unmatched.is_error
    assert "Section 1C has no equivalent in a 10-Q" in unmatched.content[0].text  # type: ignore[union-attr]


async def test_list_coverage_reports_freshness(client: Client) -> None:
    coverage = await call(client, "list_coverage")
    nvda = next(c for c in coverage["companies"] if c["ticker"] == "NVDA")
    assert nvda["latest_filed"] == "2026-08-26"
    assert "last_run" in coverage


async def test_get_financial_fact_explains_missing_facts_and_bad_arguments(client: Client) -> None:
    missing = await client.call_tool("get_financial_fact", {"ticker": "NVDA", "concept": "revenue"})
    assert missing.is_error
    assert "No XBRL facts loaded for NVDA. Run: filings facts" in missing.content[0].text  # type: ignore[union-attr]
    half = await client.call_tool(
        "get_financial_fact", {"ticker": "NVDA", "concept": "revenue", "fiscal_year": 2027}
    )
    assert "Give fiscal_year and fiscal_period together" in half.content[0].text  # type: ignore[union-attr]
    unknown = await client.call_tool("get_financial_fact", {"ticker": "XYZ", "concept": "revenue"})
    assert "XYZ is not a tracked company" in unknown.content[0].text  # type: ignore[union-attr]
