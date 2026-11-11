import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PureWindowsPath
from typing import Any

import httpx
import pytest

from goldirham_filings.cli import main, mcp_config
from goldirham_filings.companies import BY_TICKER
from goldirham_filings.config import Settings
from goldirham_filings.ingest import IngestReport

NVDA = BY_TICKER["NVDA"].cik_int


def test_mcp_config_is_valid_json_with_escaped_windows_paths() -> None:
    command = PureWindowsPath(
        r"E:\Work Space\goldirham-filings\.venv\Scripts\goldirham-filings-mcp.exe"
    )
    text = json.dumps(mcp_config(command))
    assert "\\\\Work Space\\\\" in text
    assert json.loads(text)["mcpServers"]["goldirham-filings"]["command"] == str(command)


@contextmanager
def no_database() -> Iterator[object]:
    yield object()


@pytest.fixture
def stored_facts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """CIKs whose facts reached the database, with EDGAR, Postgres and Ollama faked."""
    stored: list[int] = []

    def store(conn: Any, cik: int, facts: list[Any]) -> int:
        stored.append(cik)
        return len(facts)

    monkeypatch.setattr("goldirham_filings.cli.connect", no_database)
    monkeypatch.setattr("goldirham_filings.cli.EdgarClient", lambda *args: object())
    monkeypatch.setattr(
        "goldirham_filings.cli.update", lambda *args, **kwargs: IngestReport([], [])
    )
    monkeypatch.setattr("goldirham_filings.cli.embed_missing", lambda *args: 0)
    monkeypatch.setattr("goldirham_filings.xbrl.fetch_company_facts", lambda *args: {})
    monkeypatch.setattr("goldirham_filings.xbrl.store_facts", store)
    return stored


@pytest.mark.parametrize("command", ["facts", "update"])
def test_tickers_are_accepted_in_any_case(command: str, stored_facts: list[int]) -> None:
    main([command, "--tickers", "nvda"])
    assert stored_facts == [NVDA]


@pytest.mark.parametrize("command", ["ingest", "facts", "update"])
def test_unknown_tickers_exit_naming_them(command: str, stored_facts: list[int]) -> None:
    with pytest.raises(SystemExit) as exited:
        main([command, "--tickers", "nvda", "xyz"])
    assert exited.value.code == "unknown tickers: XYZ"


@pytest.mark.parametrize("command", ["ingest", "facts", "update"])
def test_missing_sec_contact_exits_with_the_message(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("goldirham_filings.cli.settings", lambda: Settings(sec_user_agent=""))
    with pytest.raises(SystemExit) as exited:
        main([command])
    assert exited.value.code == (
        "error: SEC_USER_AGENT must include a contact email (SEC fair-access policy)"
    )


@pytest.mark.parametrize("command", ["facts", "update"])
def test_one_companys_facts_failure_does_not_stop_the_others(
    command: str,
    stored_facts: list[int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fetch(client: Any, cik: int) -> dict[str, Any]:
        if cik == BY_TICKER["AMD"].cik_int:
            raise httpx.ConnectError("connection refused")
        return {}

    monkeypatch.setattr("goldirham_filings.xbrl.fetch_company_facts", fetch)
    with pytest.raises(SystemExit) as exited:
        main([command, "--tickers", "AMD", "NVDA"])
    assert stored_facts == [NVDA]
    assert exited.value.code not in (0, None)
    assert "FAILED   AMD" in capsys.readouterr().out


def test_foreign_facts_load_only_when_enabled(
    stored_facts: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    main(["facts", "--tickers", "TSM"])
    assert stored_facts == []
    monkeypatch.setattr(
        "goldirham_filings.cli.settings", lambda: Settings(ingest_foreign_filers=True)
    )
    main(["facts", "--tickers", "TSM"])
    assert stored_facts == [BY_TICKER["TSM"].cik_int]


def test_update_prints_its_summary_when_facts_fail(
    stored_facts: list[int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(client: Any, cik: int) -> dict[str, Any]:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("goldirham_filings.xbrl.fetch_company_facts", refuse)
    with pytest.raises(SystemExit):
        main(["update", "--tickers", "NVDA"])
    assert "1 failed" in capsys.readouterr().out
