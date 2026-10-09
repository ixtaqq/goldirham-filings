"""Golden question set: schema, offset resolution and validation against the index.

Gold spans are offsets into section text plus the quoted text. Validation re-reads the
section and checks text[start:end] == quote, so a parser change that shifts offsets fails
loudly instead of silently breaking the set.
"""

import random
from datetime import date
from pathlib import Path
from typing import Any, Literal

import psycopg
from pydantic import BaseModel

from goldirham_filings.retrieve import Filters

ItemType = Literal["lookup", "risk", "comparison", "cross-company", "unanswerable"]


class GoldSpan(BaseModel):
    accession: str
    section: str
    quote: str
    start: int | None = None
    end: int | None = None


class GoldItem(BaseModel):
    id: str
    type: ItemType
    question: str
    filters: Filters = Filters()
    answerable: bool
    gold_spans: list[GoldSpan]
    gold_answer: str
    notes: str = ""
    checked_by: str | None = None
    checked_at: date | None = None


def load_golden(path: Path) -> list[GoldItem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [GoldItem.model_validate_json(line) for line in lines if line.strip()]


def save_golden(path: Path, items: list[GoldItem]) -> None:
    path.write_text(
        "".join(item.model_dump_json(exclude_none=False) + "\n" for item in items),
        encoding="utf-8",
        newline="\n",
    )


def _section_text(
    conn: psycopg.Connection[tuple[Any, ...]], accession: str, section: str
) -> str | None:
    row = conn.execute(
        "SELECT text FROM sections WHERE accession = %s AND item = %s", (accession, section)
    ).fetchone()
    return None if row is None else str(row[0])


def resolve_offsets(
    conn: psycopg.Connection[tuple[Any, ...]], items: list[GoldItem]
) -> tuple[list[GoldItem], list[str]]:
    """Fill start/end from each span's quote. Returns updated items and problems found."""
    problems: list[str] = []
    resolved = []
    for item in items:
        spans = []
        for span in item.gold_spans:
            text = _section_text(conn, span.accession, span.section)
            where = f"{item.id} {span.accession} {span.section}"
            if text is None:
                problems.append(f"{where}: section not in index")
                spans.append(span)
                continue
            start = text.find(span.quote)
            if start < 0:
                problems.append(f"{where}: quote not found: {span.quote[:60]!r}")
                spans.append(span)
                continue
            if text.find(span.quote, start + 1) >= 0:
                problems.append(f"{where}: quote occurs more than once; using the first")
            spans.append(span.model_copy(update={"start": start, "end": start + len(span.quote)}))
        resolved.append(item.model_copy(update={"gold_spans": spans}))
    return resolved, problems


def validate(conn: psycopg.Connection[tuple[Any, ...]], items: list[GoldItem]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            problems.append(f"{item.id}: duplicate id")
        seen.add(item.id)
        if item.answerable != (item.type != "unanswerable"):
            problems.append(f"{item.id}: answerable must be false exactly for type unanswerable")
        if item.answerable and not item.gold_spans:
            problems.append(f"{item.id}: answerable item has no gold spans")
        for span in item.gold_spans:
            text = _section_text(conn, span.accession, span.section)
            where = f"{item.id} {span.accession} {span.section}"
            if text is None:
                problems.append(f"{where}: section not in index")
            elif span.start is None or span.end is None:
                problems.append(f"{where}: offsets not resolved")
            elif text[span.start : span.end] != span.quote:
                problems.append(f"{where}: offsets {span.start}-{span.end} no longer match quote")
    return problems


SplitName = Literal["dev", "test"]


def make_split(items: list[GoldItem], seed: int, test_fraction: float) -> dict[str, Any]:
    """Stratified by item type: each type contributes round(n * test_fraction) test items,
    chosen by a seeded shuffle of its sorted ids, so the split is reproducible."""
    rng = random.Random(seed)
    test: list[str] = []
    for item_type in sorted({i.type for i in items}):
        ids = sorted(i.id for i in items if i.type == item_type)
        rng.shuffle(ids)
        test.extend(ids[: round(len(ids) * test_fraction)])
    dev = sorted(i.id for i in items if i.id not in set(test))
    return {"seed": seed, "test_fraction": test_fraction, "dev": dev, "test": sorted(test)}


def select(items: list[GoldItem], split: dict[str, Any], name: SplitName | None) -> list[GoldItem]:
    """Items in one split, in golden-set order; None keeps every item."""
    if name is None:
        return items
    wanted = set(split[name])
    missing = wanted - {i.id for i in items}
    if missing:
        raise ValueError(f"split names items not in the golden set: {sorted(missing)}")
    return [i for i in items if i.id in wanted]
