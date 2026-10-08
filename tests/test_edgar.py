from datetime import date

import httpx
import pytest

from goldirham_filings.edgar import EdgarClient, FilingRef, parse_submission_page

UA = "Test Runner test@example.com"


def client(handler: httpx.MockTransport) -> EdgarClient:
    return EdgarClient(UA, requests_per_second=1000, retry_base_seconds=0, transport=handler)


def test_retries_rate_limit_then_returns_body_and_sends_contact_header() -> None:
    seen: list[tuple[int, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((len(seen), request.headers["User-Agent"]))
        return httpx.Response(429 if len(seen) < 3 else 200, text="ok")

    assert client(httpx.MockTransport(handle)).get("https://www.sec.gov/x").text == "ok"
    assert seen == [(0, UA), (1, UA), (2, UA)]


def test_gives_up_after_max_attempts_with_the_last_status() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError, match="503"):
        client(httpx.MockTransport(handle)).get("https://www.sec.gov/x")
    assert len(calls) == 4


def test_client_error_is_not_retried() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError, match="404"):
        client(httpx.MockTransport(handle)).get("https://www.sec.gov/x")
    assert len(calls) == 1


def test_user_agent_without_email_is_rejected() -> None:
    with pytest.raises(ValueError, match="contact email"):
        EdgarClient("Goldirham Filings")


def test_submission_page_keeps_10k_and_10q_only_and_builds_document_url() -> None:
    page = {
        "form": ["4", "10-Q", "10-K/A", "10-K"],
        "accessionNumber": ["a-1", "0001045810-26-000075", "a-3", "0001045810-26-000021"],
        "filingDate": ["2026-09-01", "2026-08-26", "2026-03-01", "2026-02-25"],
        "reportDate": ["", "2026-07-26", "2026-01-25", ""],
        "primaryDocument": ["x.xml", "nvda-20260726.htm", "a.htm", "nvda-20260125.htm"],
    }
    refs = list(parse_submission_page(1045810, page))
    assert refs == [
        FilingRef("0001045810-26-000075", 1045810, "10-Q", date(2026, 8, 26), date(2026, 7, 26),
                  "nvda-20260726.htm"),
        FilingRef("0001045810-26-000021", 1045810, "10-K", date(2026, 2, 25), None,
                  "nvda-20260125.htm"),
    ]  # fmt: skip
    assert refs[0].url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda-20260726.htm"
    )
