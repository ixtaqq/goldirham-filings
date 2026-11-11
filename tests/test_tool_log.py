import json

import httpx
import psycopg
import pytest
from mcp import Client

from goldirham_filings.answer import AnswerResult
from goldirham_filings.embed import OllamaEmbedder
from goldirham_filings.server import build_server, logged


def unreachable_database() -> psycopg.Connection[tuple[object, ...]]:
    raise psycopg.OperationalError("connection refused")


class NoChat:
    def complete_json(self, system: str, user: str, schema: dict[str, object]) -> str:
        raise AssertionError("not reached")


def refusing(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.anyio
async def test_a_failed_tool_call_logs_one_line_without_the_query(
    capsys: pytest.CaptureFixture[str],
) -> None:
    embedder = OllamaEmbedder(
        "nomic-embed-text", "http://x", transport=httpx.MockTransport(refusing)
    )
    server = build_server(unreachable_database, embedder, NoChat())
    async with Client(server) as client:
        await client.call_tool("search_filings", {"query": "a private question"})
    err = capsys.readouterr().err
    line = json.loads(err)
    assert (line["event"], line["tool"], line["outcome"]) == (
        "tool_call",
        "search_filings",
        "error",
    )
    assert line["error"] == "ToolError"
    assert line["ms"] >= 0
    assert "a private question" not in err


def test_an_abstention_logs_its_reason_and_passage_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def answer_question() -> AnswerResult:
        return AnswerResult(
            status="abstained",
            question="a private question",
            sentences=[],
            passages=[],
            notes=["no indexed filing matches the question"],
        )

    assert logged(answer_question)().status == "abstained"
    err = capsys.readouterr().err
    line = json.loads(err)
    assert line["outcome"] == "abstained"
    assert line["reason"] == "no indexed filing matches the question"
    assert line["passages"] == 0
    assert "a private question" not in err


@pytest.mark.anyio
async def test_logging_keeps_each_tools_name_and_parameters() -> None:
    embedder = OllamaEmbedder(
        "nomic-embed-text", "http://x", transport=httpx.MockTransport(refusing)
    )
    server = build_server(unreachable_database, embedder, NoChat())
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert "get_passage" in tools
    assert "query" in tools["search_filings"].input_schema["properties"]
    assert tools["search_filings"].description
