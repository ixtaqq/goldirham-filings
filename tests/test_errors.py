import httpx
import psycopg
import pytest
from mcp import Client

from goldirham_filings.answer import OllamaChat
from goldirham_filings.cli import main
from goldirham_filings.config import Settings
from goldirham_filings.embed import OllamaEmbedder
from goldirham_filings.errors import DependencyError, database_error
from goldirham_filings.server import build_server
from goldirham_filings.server import main as server_main

URL = "http://127.0.0.1:11434"


def refusing(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def timing_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def model_missing(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={"error": "model 'qwen3:8b' not found"})


def server_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "out of memory"})


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (refusing, "Ollama is not reachable at http://127.0.0.1:11434"),
        (timing_out, "did not respond in time"),
        (model_missing, "not pulled. Run: ollama pull qwen3:8b"),
        (server_error, "returned HTTP 500 for qwen3:8b"),
    ],
)
def test_ollama_failures_become_dependency_errors(handler: object, expected: str) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    chat = OllamaChat("qwen3:8b", URL, transport=transport)
    with pytest.raises(DependencyError, match=expected):
        chat.complete_json("system", "user", {})
    embedder = OllamaEmbedder("nomic-embed-text", URL, transport=transport)
    with pytest.raises(DependencyError):
        embedder.embed_query("x")


def test_unsupported_embedding_model_names_the_supported_ones() -> None:
    with pytest.raises(DependencyError) as raised:
        OllamaEmbedder("all-minilm", URL)
    assert "all-minilm" in str(raised.value)
    assert "nomic-embed-text" in str(raised.value)


def test_server_exits_with_the_message_on_an_unsupported_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "goldirham_filings.server.settings", lambda: Settings(embedding_model="all-minilm")
    )
    monkeypatch.setattr("goldirham_filings.server.startup_warnings", lambda *args: [])
    with pytest.raises(SystemExit) as exited:
        server_main()
    assert "all-minilm" in str(exited.value.code)


def test_database_errors_name_the_fix_without_the_password() -> None:
    url = "postgresql://filings:secret@127.0.0.1:5442/filings"
    down = database_error(psycopg.OperationalError("connection refused"), url)
    assert "127.0.0.1:5442/filings is not reachable" in str(down)
    assert "docker compose up -d --wait" in str(down)
    assert "secret" not in str(down)
    empty = database_error(psycopg.errors.UndefinedTable("relation chunks"), url)
    assert "alembic upgrade head" in str(empty)


def unreachable_database() -> psycopg.Connection[tuple[object, ...]]:
    raise psycopg.OperationalError("connection refused")


class NoChat:
    def complete_json(self, system: str, user: str, schema: dict[str, object]) -> str:
        raise AssertionError("not reached")


@pytest.mark.anyio
async def test_tools_report_a_down_database_as_a_clear_error() -> None:
    embedder = OllamaEmbedder("nomic-embed-text", URL, transport=httpx.MockTransport(refusing))
    server = build_server(unreachable_database, embedder, NoChat())
    async with Client(server) as client:
        for tool, arguments in [
            ("search_filings", {"query": "export controls"}),
            ("answer_question", {"question": "What changed?"}),
            ("list_coverage", {}),
        ]:
            result = await client.call_tool(tool, arguments)
            assert result.is_error
            text = result.content[0].text  # type: ignore[union-attr]
            assert "is not reachable. Start Docker Desktop" in text, text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("accessions", "expected"),
    [
        ({"older_accession": "A"}, "together"),
        ({"newer_accession": "A"}, "together"),
        ({"older_accession": "A", "newer_accession": "A"}, "different filings"),
    ],
)
async def test_compare_filings_needs_two_different_accessions(
    accessions: dict[str, str], expected: str
) -> None:
    embedder = OllamaEmbedder("nomic-embed-text", URL, transport=httpx.MockTransport(refusing))
    server = build_server(unreachable_database, embedder, NoChat())
    async with Client(server) as client:
        result = await client.call_tool(
            "compare_filings", {"ticker": "NVDA", "section": "1A", **accessions}
        )
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert expected in text, text


LONG = "x" * 2001


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_filings", {"query": LONG}),
        ("answer_question", {"question": LONG}),
        ("compare_filings", {"ticker": "NVDA", "section": "1A", "focus": LONG}),
        ("search_filings", {"query": "x", "tickers": ["NVDA"] * 33}),
        ("search_filings", {"query": "x", "sections": ["1A"] * 33}),
        ("answer_question", {"question": "x", "forms": ["10-K"] * 33}),
    ],
)
async def test_oversized_inputs_are_refused_before_the_database(
    tool: str, arguments: dict[str, object]
) -> None:
    embedder = OllamaEmbedder("nomic-embed-text", URL, transport=httpx.MockTransport(refusing))
    server = build_server(unreachable_database, embedder, NoChat())
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert "is not reachable" not in text, text


@pytest.mark.anyio
async def test_a_query_at_the_limit_is_accepted() -> None:
    embedder = OllamaEmbedder("nomic-embed-text", URL, transport=httpx.MockTransport(refusing))
    server = build_server(unreachable_database, embedder, NoChat())
    async with Client(server) as client:
        result = await client.call_tool("search_filings", {"query": "x" * 2000})
    text = result.content[0].text  # type: ignore[union-attr]
    assert "is not reachable" in text, text


def test_cli_exits_with_the_message_when_ollama_is_down(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise DependencyError("Ollama is not reachable at http://x. Start Ollama.")

    monkeypatch.setattr("goldirham_filings.cli.cmd_search", fail)
    with pytest.raises(SystemExit) as exited:
        main(["search", "anything"])
    assert exited.value.code == "error: Ollama is not reachable at http://x. Start Ollama."
