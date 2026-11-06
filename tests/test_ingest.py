from datetime import date
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from goldirham_filings import ingest
from goldirham_filings.companies import BY_TICKER
from goldirham_filings.edgar import FilingRef
from goldirham_filings.parse import PARSER_VERSION

FILLER = "Demand for accelerated computing grew. " * 40
HTML = f"""<html><body>
<p>Item 1. Business</p><p>{FILLER}</p>
<p>Item 1A. Risk Factors</p><p>{FILLER}</p>
<p>Item 7. Management's Discussion and Analysis</p><p>{FILLER}</p>
</body></html>""".encode()
REF = FilingRef("TEST-0000000001", 1045810, "10-K", date(2026, 2, 25), date(2026, 1, 25), "t.htm")


def counts(conn: psycopg.Connection[tuple[Any, ...]]) -> tuple[int, int, str]:
    row = conn.execute(
        """
        SELECT count(DISTINCT s.id), count(c.id), min(f.parser_version)
        FROM filings f JOIN sections s USING (accession) JOIN chunks c ON c.section_id = s.id
        WHERE f.accession = %s
        """,
        (REF.accession,),
    ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


@pytest.mark.db
def test_index_is_idempotent_and_reindexes_on_parser_version_bump(
    fixture_conn: psycopg.Connection[tuple[Any, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    company = BY_TICKER["NVDA"]
    try:
        first = ingest.index_filing(fixture_conn, company, REF, HTML, Path("t.htm"))
        assert (first.status, first.sections, first.missing) == ("indexed", 3, ())
        assert counts(fixture_conn) == (3, first.chunks, PARSER_VERSION)

        again = ingest.index_filing(fixture_conn, company, REF, HTML, Path("t.htm"))
        assert again.status == "skipped"
        assert counts(fixture_conn) == (3, first.chunks, PARSER_VERSION)

        monkeypatch.setattr(ingest, "PARSER_VERSION", "test-next")
        bumped = ingest.index_filing(fixture_conn, company, REF, HTML, Path("t.htm"))
        assert bumped.status == "replaced"
        assert counts(fixture_conn) == (3, first.chunks, "test-next")
    finally:
        fixture_conn.rollback()
        fixture_conn.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
        fixture_conn.commit()


class FlakyEdgar:
    """NVDA lists a good and a missing filing; AMD's filing list is refused."""

    BAD = FilingRef("TEST-0000000002", 1045810, "10-Q", date(2026, 5, 27), date(2026, 4, 26),
                    "missing.htm")  # fmt: skip

    def filings(self, cik: int, limits: dict[str, int]) -> list[FilingRef]:
        if cik == BY_TICKER["AMD"].cik_int:
            request = httpx.Request("GET", "https://data.sec.gov/submissions")
            raise httpx.HTTPStatusError("403", request=request,
                                        response=httpx.Response(403, request=request))  # fmt: skip
        return [self.BAD, REF]

    def get(self, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/missing.htm"):
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("404 Not Found", request=request, response=response)
        return httpx.Response(200, content=HTML, request=request)


@pytest.mark.db
def test_failed_filings_are_reported_and_skipped(
    fixture_conn: psycopg.Connection[tuple[Any, ...]], tmp_path: Path
) -> None:
    try:
        report = ingest.ingest(fixture_conn, FlakyEdgar(), tmp_path,  # type: ignore[arg-type]
                               [BY_TICKER["NVDA"], BY_TICKER["AMD"]], {"10-K": 1})  # fmt: skip
        assert [(r.ref.accession, r.status) for r in report.results] == [(REF.accession, "indexed")]
        assert [(f.ticker, f.accession) for f in report.failures] == [
            ("NVDA", "TEST-0000000002"),
            ("AMD", ""),
        ]
        assert report.failures[0].error.startswith("HTTPStatusError: 404 Not Found")
        assert "SEC refused the request (HTTP 403)" in report.failures[1].error
        assert report.summary() == (
            "1 filings (1 indexed, 0 replaced, 0 skipped); 0 with missing required sections; "
            "2 failed"
        )
    finally:
        fixture_conn.rollback()
        fixture_conn.execute(
            "DELETE FROM filings WHERE accession = ANY(%s)",
            ([REF.accession, FlakyEdgar.BAD.accession],),
        )
        fixture_conn.commit()
