"""Start the real stdio server (as Claude Desktop would) and call every tool once.

Needs the dev database and Ollama. Each call has a check on its structured result; prints
one block per call and exits non-zero if any call errors or fails its check.
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
SERVER = StdioServerParameters(
    command=str(ROOT / ".venv" / "Scripts" / "goldirham-filings-mcp.exe"), cwd=str(ROOT)
)
NVDA_LATEST_10Q_PERIOD = "2026-07-26"

Check = Callable[[dict[str, Any]], bool]
CALLS: list[tuple[str, dict[str, Any], Check]] = [
    ("list_coverage", {}, lambda r: len(r["companies"]) == 12),
    (
        "search_filings",
        {"query": "export controls China", "tickers": ["AMAT"], "k": 3},
        lambda r: len(r["passages"]) == 3 and all(p["ticker"] == "AMAT" for p in r["passages"]),
    ),
    (
        "compare_filings",
        {"ticker": "MSFT", "section": "1A", "k": 2},
        lambda r: r["older"]["period_end"] < r["newer"]["period_end"] and len(r["changed"]) == 2,
    ),
    (
        "compare_filings",
        {"ticker": "MU", "section": "1A", "focus": "customer concentration"},
        lambda r: bool(r["older_passages"]) and bool(r["newer_passages"]),
    ),
    (
        "answer_question",
        {"question": "What is Broadcom's 2030 revenue target?", "tickers": ["AVGO"]},
        lambda r: r["status"] == "abstained",
    ),
    (
        "answer_question",
        {"question": "What was Data Center revenue in NVIDIA's most recent 10-Q?",
         "tickers": ["NVDA"]},
        lambda r: r["status"] == "answered"
        and {p["period_end"] for p in r["passages"]} == {NVDA_LATEST_10Q_PERIOD},
    ),
    (
        "get_financial_fact",
        {"ticker": "AMD", "concept": "revenue", "fiscal_year": 2026, "fiscal_period": "Q3"},
        lambda r: any(f["value"] in (13377000000, "13377000000") and f["duration_days"] == 91
                      and f["form"] == "10-Q" for f in r["facts"]),
    ),
]  # fmt: skip


async def main() -> int:
    failures = 0
    async with Client(SERVER, read_timeout_seconds=600) as client:
        tools = await client.list_tools()
        print("tools:", sorted(t.name for t in tools.tools))
        first_chunk = None
        for name, arguments, check in CALLS:
            result = await client.call_tool(name, arguments)
            content = result.structured_content or {}
            ok = not result.is_error and check(content)
            failures += not ok
            if name == "search_filings" and content.get("passages"):
                first_chunk = content["passages"][0]["chunk_id"]
            print(f"\n## {name} {json.dumps(arguments)} ok={ok}")
            print(json.dumps(content, default=str)[:800])
        if first_chunk is not None:
            result = await client.call_tool("get_passage", {"chunk_id": first_chunk})
            content = result.structured_content or {}
            ok = not result.is_error and content["passage"]["chunk_id"] == first_chunk
            failures += not ok
            print(f"\n## get_passage chunk_id={first_chunk} ok={ok}")
    print(f"\n{failures} failed checks")
    return failures


if __name__ == "__main__":
    sys.exit(anyio.run(main))
