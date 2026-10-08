from datetime import date
from pathlib import Path
from typing import Any

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
