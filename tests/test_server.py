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
            {"text": "Data Center revenue was $89.0 billion.", "citations": [1]}]})  # fmt: skip


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    server = build_server(lambda: connect(url), FixtureEmbedder(), CitingChat())
    async with Client(server) as c:
        yield c


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content
    return content


async def test_all_five_tools_are_listed_read_only(client: Client) -> None:
    tools = (await client.list_tools()).tools
    assert sorted(t.name for t in tools) == [
        "answer_question", "compare_filings", "get_passage", "list_coverage", "search_filings",
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
