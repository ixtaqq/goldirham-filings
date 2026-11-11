"""p50 and p95 of search_filings and get_financial_fact, without Ollama or EDGAR.

Search runs through the MCP tool over the fixture corpus with its precomputed question
vectors. The fixture has no XBRL facts, so the fact lookup times synthetic rows at several
sizes, inserted in one transaction and rolled back. Writes benchmarks/latency-<date>.json.

  $env:TEST_DATABASE_URL = '...filings_test'; python scripts/measure_latency.py
"""

import json
import os
import statistics
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import anyio
from mcp import Client

from goldirham_filings.companies import BY_TICKER
from goldirham_filings.config import ROOT
from goldirham_filings.db import connect
from goldirham_filings.fixtures import FixtureEmbedder
from goldirham_filings.server import build_server
from goldirham_filings.xbrl import company_facts, lookup

SEARCH_ROUNDS = 5
FACT_SAMPLES = 20
FACT_ROW_COUNTS = (10_000, 50_000, 100_000)
CONCEPTS = 500


class NoChat:
    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        raise AssertionError("search and fact lookups do not call the model")


def percentiles(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    return {
        "samples": len(ordered),
        "p50_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))], 1),
    }


def timed(call: Callable[[], object]) -> float:
    started = time.perf_counter()
    call()
    return (time.perf_counter() - started) * 1000


async def search_samples(url: str) -> list[float]:
    embedder = FixtureEmbedder()
    server = build_server(lambda: connect(url, read_only=True), embedder, NoChat())
    questions = json.loads(
        (ROOT / "evals" / "fixtures" / "manifest.json").read_text(encoding="utf-8")
    )["questions"]
    samples: list[float] = []
    async with Client(server) as client:
        for _ in range(SEARCH_ROUNDS):
            for question in questions:
                started = time.perf_counter()
                result = await client.call_tool("search_filings", {"query": question, "k": 8})
                assert not result.is_error, result.content
                samples.append((time.perf_counter() - started) * 1000)
    return samples


def fact_samples(url: str, rows: int) -> list[float]:
    company = BY_TICKER["NVDA"]
    start = date(2010, 1, 31)
    per_concept = rows // CONCEPTS
    values = [
        (company.cik_int, "us-gaap", f"Concept{c}", f"Concept {c}", "USD",
         start + timedelta(days=91 * p - 90), start + timedelta(days=91 * p), 1000 + p,
         f"0000000000-00-{p:06d}", "10-Q", 2010 + p // 4, "Q1", start + timedelta(days=91 * p + 30))
        for c in range(CONCEPTS)
        for p in range(per_concept)
    ]  # fmt: skip
    with connect(url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO xbrl_facts (cik, taxonomy, concept, label, unit, period_start,
                                        period_end, value, accession, form, fiscal_year,
                                        fiscal_period, filed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                values,
            )
        samples = [
            timed(lambda: lookup(company_facts(conn, company.cik_int), "NVDA", "Concept1"))
            for _ in range(FACT_SAMPLES)
        ]
        conn.rollback()
    return samples


def main() -> None:
    url = os.environ["TEST_DATABASE_URL"]
    report: dict[str, Any] = {
        "corpus": "CI fixture corpus; the dev index was not available",
        "search_filings": percentiles(anyio.run(search_samples, url)),
        "get_financial_fact_synthetic_rows": {
            str(rows): percentiles(fact_samples(url, rows)) for rows in FACT_ROW_COUNTS
        },
    }
    out = ROOT / "benchmarks" / f"latency-{date.today().isoformat()}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
