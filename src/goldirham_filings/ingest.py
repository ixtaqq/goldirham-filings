"""EDGAR -> raw HTML on disk -> sections -> chunks in Postgres. Safe to rerun.

A filing is keyed by accession. Raw HTML is fetched once and kept under data/raw. A filing
already indexed with the current PARSER_VERSION is skipped; an older version is replaced,
which cascades to its sections, chunks and embeddings. Each filing commits on its own, so
a failure partway through a run keeps every filing indexed before it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

M1_TICKERS = (
    "NVDA", "AMD", "AVGO", "MSFT", "AMZN", "GOOGL", "META", "INTC", "MU", "AMAT", "LRCX", "KLAC",
)  # fmt: skip
DEFAULT_LIMITS: dict[Form, int] = {"10-K": 2, "10-Q": 6}


@dataclass(frozen=True)
class IndexResult:
    ticker: str
    ref: FilingRef
    status: str
    sections: int = 0
    chunks: int = 0
    missing: tuple[str, ...] = ()


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
) -> list[IndexResult]:
    upsert_companies(conn)
    results = []
    for company in companies:
        if company.filer_type != "domestic":
            continue
        for ref in client.filings(company.cik_int, limits):
            path = raw_path(data_dir, ref)
            result = index_filing(conn, company, ref, fetch_raw(client, ref, path), path)
            results.append(result)
            print(
                f"{result.status:8} {company.ticker:5} {ref.form} {ref.period_end} "
                f"{ref.accession} sections={result.sections} chunks={result.chunks}"
                + (f" MISSING={','.join(result.missing)}" if result.missing else "")
            )
    return results
