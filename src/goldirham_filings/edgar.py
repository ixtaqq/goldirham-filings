"""Polite EDGAR client: real contact in User-Agent, rate limit, timeouts, bounded retries."""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from itertools import takewhile
from typing import Any, Literal, cast

import httpx

Form = Literal["10-K", "10-Q"]
FORMS: tuple[Form, ...] = ("10-K", "10-Q")
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class FilingRef:
    accession: str
    cik: int
    form: Form
    filed_at: date
    period_end: date | None
    primary_document: str

    @property
    def url(self) -> str:
        folder = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{folder}/{self.primary_document}"
        )


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        requests_per_second: float = 6.0,
        max_attempts: int = 4,
        retry_base_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT must include a contact email (SEC fair-access policy)")
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
        )
        self._min_interval = 1.0 / requests_per_second
        self._last_request = 0.0
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds

    def close(self) -> None:
        self._http.close()

    def get(self, url: str) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                response = self._http.get(url)
            except httpx.TransportError:
                if attempt == self._max_attempts:
                    raise
            else:
                if response.status_code not in RETRY_STATUSES or attempt == self._max_attempts:
                    response.raise_for_status()
                    return response
            time.sleep(self._retry_base_seconds * 2.0**attempt)
        raise AssertionError("unreachable")

    def get_json(self, url: str) -> dict[str, Any]:
        return cast(dict[str, Any], self.get(url).json())

    def company_tickers(self) -> dict[str, int]:
        data = self.get_json("https://www.sec.gov/files/company_tickers.json")
        return {row["ticker"]: int(row["cik_str"]) for row in data.values()}

    def filer_forms(self, cik: int) -> set[str]:
        """Every form type in the company's recent submissions, used for the filer-type check."""
        recent = self.get_json(_submissions_url(cik))["filings"]["recent"]
        return set(recent["form"])

    def filings(self, cik: int, limits: dict[Form, int]) -> list[FilingRef]:
        """Latest filings per form, newest first, walking older submission pages as needed."""
        remaining = dict(limits)
        found: list[FilingRef] = []
        for ref in self._iter_filings(cik):
            if remaining.get(ref.form, 0) > 0:
                found.append(ref)
                remaining[ref.form] -= 1
            if not any(remaining.values()):
                break
        return found

    def filings_since(self, cik: int, since: date) -> list[FilingRef]:
        """10-K and 10-Q filings filed on or after `since`, newest first. Submission pages
        are ordered by filing date, so the walk stops at the first older filing."""
        return list(takewhile(lambda ref: ref.filed_at >= since, self._iter_filings(cik)))

    def _iter_filings(self, cik: int) -> Iterator[FilingRef]:
        data = self.get_json(_submissions_url(cik))
        yield from parse_submission_page(cik, data["filings"]["recent"])
        for page in data["filings"].get("files", []):
            older = self.get_json(f"https://data.sec.gov/submissions/{page['name']}")
            yield from parse_submission_page(cik, older)


def _submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik:010d}.json"


def parse_submission_page(cik: int, page: dict[str, list[str]]) -> Iterator[FilingRef]:
    """EDGAR pages are column-oriented arrays. Amendments (10-K/A) are skipped on purpose."""
    for i, form in enumerate(page["form"]):
        if form not in FORMS:
            continue
        report_date = page["reportDate"][i]
        yield FilingRef(
            accession=page["accessionNumber"][i],
            cik=cik,
            form=form,
            filed_at=date.fromisoformat(page["filingDate"][i]),
            period_end=date.fromisoformat(report_date) if report_date else None,
            primary_document=page["primaryDocument"][i],
        )
