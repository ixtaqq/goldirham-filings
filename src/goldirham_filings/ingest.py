"""EDGAR -> raw HTML on disk -> sections -> chunks in Postgres. Safe to rerun.

A filing is keyed by accession. Raw HTML is fetched once and kept under data/raw. A filing
already indexed with the current PARSER_VERSION is skipped; an older version is replaced,
which cascades to its sections, chunks and embeddings. Each filing commits on its own. A
filing (or a company's filing list) that fails to fetch or parse is recorded and skipped,
so one bad document does not end the run; losing the database does.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg

from goldirham_filings.chunk import chunk_section
from goldirham_filings.companies import TRACKED_COMPANIES, Company
from goldirham_filings.edgar import EdgarClient, FilingRef, Form
from goldirham_filings.parse import (
    PARSER_VERSION,
    decode_html,
    fiscal_focus,
    html_to_text,
    missing_required,
    split_sections,
)

DEFAULT_LIMITS: dict[Form, int] = {"10-K": 2, "10-Q": 6, "20-F": 2}


@dataclass(frozen=True)
class IndexResult:
    ticker: str
    ref: FilingRef
    status: str
    sections: int = 0
    chunks: int = 0
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngestFailure:
    ticker: str
    accession: str  # empty when the company's filing list could not be read
    error: str


@dataclass(frozen=True)
class IngestReport:
    results: list[IndexResult]
    failures: list[IngestFailure]

    def summary(self) -> str:
        statuses = [r.status for r in self.results]
        counts = ", ".join(f"{statuses.count(s)} {s}" for s in ("indexed", "replaced", "skipped"))
        missing = sum(bool(r.missing) for r in self.results)
        return (f"{len(self.results)} filings ({counts}); {missing} with missing required "
                f"sections; {len(self.failures)} failed")  # fmt: skip


def describe_failure(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 403:
        return ("SEC refused the request (HTTP 403). Check that SEC_USER_AGENT in .env is your "
                "real name and email, and that requests stay under the rate limit")  # fmt: skip
    return f"{type(error).__name__}: {error}"


def raw_path(data_dir: Path, ref: FilingRef) -> Path:
    return data_dir / "raw" / str(ref.cik) / f"{ref.accession}.htm"


def fetch_raw(client: EdgarClient, ref: FilingRef, path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    raw = client.get(ref.url).content
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    partial.write_bytes(raw)
    partial.replace(path)
    return raw


def upsert_companies(conn: psycopg.Connection[tuple[Any, ...]]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO companies (cik, ticker, name, tier, filer_type)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (cik) DO UPDATE SET ticker = EXCLUDED.ticker, name = EXCLUDED.name,
                tier = EXCLUDED.tier, filer_type = EXCLUDED.filer_type
            """,
            [(c.cik_int, c.ticker, c.name, c.tier, c.filer_type) for c in TRACKED_COMPANIES],
        )
    conn.commit()


def index_filing(
    conn: psycopg.Connection[tuple[Any, ...]],
    company: Company,
    ref: FilingRef,
    raw: bytes,
    path: Path,
) -> IndexResult:
    html = decode_html(raw)
    fiscal = fiscal_focus(html)
    row = conn.execute(
        "SELECT parser_version, missing_sections FROM filings WHERE accession = %s",
        (ref.accession,),
    ).fetchone()
    if row is not None and row[0] == PARSER_VERSION:
        # Converge metadata added after a filing was indexed without re-chunking it.
        conn.execute(
            "UPDATE filings SET fiscal_year = %s, fiscal_period = %s WHERE accession = %s",
            (fiscal.year, fiscal.period, ref.accession),
        )
        conn.commit()
        return IndexResult(company.ticker, ref, "skipped", missing=tuple(row[1]))

    sections = split_sections(html_to_text(html), ref.form)
    missing = missing_required(sections, ref.form)
    chunk_count = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM filings WHERE accession = %s", (ref.accession,))
        cur.execute(
            """
            INSERT INTO filings (accession, cik, form, period_end, filed_at, source_url,
                                 raw_path, parser_version, missing_sections, fiscal_year,
                                 fiscal_period)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (ref.accession, ref.cik, ref.form, ref.period_end, ref.filed_at, ref.url,
             str(path), PARSER_VERSION, missing, fiscal.year, fiscal.period),
        )  # fmt: skip
        for section in sections:
            cur.execute(
                "INSERT INTO sections (accession, item, title, text) VALUES (%s, %s, %s, %s)"
                " RETURNING id",
                (ref.accession, section.item, section.title, section.text),
            )
            inserted = cur.fetchone()
            assert inserted is not None
            chunks = chunk_section(section.text)
            cur.executemany(
                "INSERT INTO chunks (section_id, ordinal, start_char, end_char, text, has_table)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [(inserted[0], c.ordinal, c.start_char, c.end_char, c.text, c.has_table)
                 for c in chunks],
            )  # fmt: skip
            chunk_count += len(chunks)
    conn.commit()
    status = "replaced" if row is not None else "indexed"
    return IndexResult(company.ticker, ref, status, len(sections), chunk_count, tuple(missing))


def ingest(
    conn: psycopg.Connection[tuple[Any, ...]],
    client: EdgarClient,
    data_dir: Path,
    companies: list[Company],
    limits: dict[Form, int],
    include_foreign: bool = False,
) -> IngestReport:
    """The latest filings of each form, up to `limits`, for each domestic company, and each
    foreign private issuer when asked."""
    started = datetime.now(UTC)
    upsert_companies(conn)
    results: list[IndexResult] = []
    failures: list[IngestFailure] = []
    for company in companies:
        if company.filer_type != "domestic" and not include_foreign:
            continue
        try:
            refs = client.filings(company.cik_int, limits)
        except Exception as error:
            failures.append(IngestFailure(company.ticker, "", describe_failure(error)))
            print(f"FAILED   {company.ticker:5} filing list: {failures[-1].error}")
            continue
        _index_refs(conn, client, data_dir, company, refs, results, failures)
    report = IngestReport(results, failures)
    record_run(conn, "ingest", started, report)
    return report


def update(
    conn: psycopg.Connection[tuple[Any, ...]],
    client: EdgarClient,
    data_dir: Path,
    companies: list[Company],
    include_foreign: bool = False,
) -> IngestReport:
    """Filings newer than the newest one indexed for each company. A company with nothing
    indexed yet is skipped with a message: `filings ingest` chooses how far back to go."""
    started = datetime.now(UTC)
    latest = dict(
        conn.execute("SELECT f.cik, max(f.filed_at) FROM filings f GROUP BY f.cik").fetchall()
    )
    results: list[IndexResult] = []
    failures: list[IngestFailure] = []
    for company in companies:
        since = latest.get(company.cik_int)
        if company.filer_type != "domestic" and not include_foreign:
            continue
        if since is None:
            print(f"skipped  {company.ticker:5} nothing indexed yet; run filings ingest")
            continue
        try:
            refs = client.filings_since(company.cik_int, since)
        except Exception as error:
            failures.append(IngestFailure(company.ticker, "", describe_failure(error)))
            print(f"FAILED   {company.ticker:5} filing list: {failures[-1].error}")
            continue
        _index_refs(conn, client, data_dir, company, refs, results, failures)
    report = IngestReport(results, failures)
    record_run(conn, "update", started, report)
    return report


def record_run(
    conn: psycopg.Connection[tuple[Any, ...]], command: str, started: datetime, report: IngestReport
) -> None:
    indexed = sum(r.status in ("indexed", "replaced") for r in report.results)
    conn.execute(
        "INSERT INTO index_runs (command, started_at, filings_indexed, failures)"
        " VALUES (%s, %s, %s, %s)",
        (command, started, indexed, len(report.failures)),
    )
    conn.commit()


def _index_refs(
    conn: psycopg.Connection[tuple[Any, ...]],
    client: EdgarClient,
    data_dir: Path,
    company: Company,
    refs: list[FilingRef],
    results: list[IndexResult],
    failures: list[IngestFailure],
) -> None:
    for ref in refs:
        path = raw_path(data_dir, ref)
        try:
            result = index_filing(conn, company, ref, fetch_raw(client, ref, path), path)
        except psycopg.OperationalError:
            raise
        except Exception as error:
            conn.rollback()
            failures.append(IngestFailure(company.ticker, ref.accession,
                                          describe_failure(error)))  # fmt: skip
            print(f"FAILED   {company.ticker:5} {ref.form} {ref.period_end} "
                  f"{ref.accession}: {failures[-1].error}")  # fmt: skip
            continue
        results.append(result)
        print(
            f"{result.status:8} {company.ticker:5} {ref.form} {ref.period_end} "
            f"{ref.accession} sections={result.sections} chunks={result.chunks}"
            + (f" MISSING={','.join(result.missing)}" if result.missing else "")
        )
