"""Read side of the index: hybrid search, passage lookup, filing comparison, coverage."""

import re
from collections.abc import Sequence
from datetime import date, datetime
from itertools import zip_longest
from typing import Any, Literal

import numpy as np
import psycopg
from pydantic import BaseModel

from goldirham_filings.companies import BY_TICKER
from goldirham_filings.edgar import Form
from goldirham_filings.embed import Embedder
from goldirham_filings.scope import parse_references, resolve_accessions, section_intent

SearchMode = Literal["hybrid", "keyword", "vector"]
RRF_K = 60
# Applied only when neither the question nor the caller names filings or periods.
RECENCY_DECAY = 0.9
MAX_SPAN_CHARS = 20000

PASSAGE_COLUMNS = """
    c.id, co.ticker, co.name, f.form, f.accession, f.period_end, f.filed_at, s.item, s.title,
    c.start_char, c.end_char, c.text, f.source_url, f.fiscal_year, f.fiscal_period
"""
PASSAGE_FROM = """
    chunks c
    JOIN sections s ON s.id = c.section_id
    JOIN filings f ON f.accession = s.accession
    JOIN companies co ON co.cik = f.cik
"""


class Filters(BaseModel):
    tickers: list[str] | None = None
    forms: list[Form] | None = None
    period_from: date | None = None
    period_to: date | None = None
    sections: list[str] | None = None
    accessions: list[str] | None = None


class Passage(BaseModel):
    chunk_id: int
    ticker: str
    company: str
    form: str
    accession: str
    period_end: date | None
    filed_at: date
    section: str
    section_title: str
    start_char: int
    end_char: int
    text: str
    source_url: str
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    score: float = 0.0
    similarity: float | None = None


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))


def prefer_recent(ids: list[int], ages: dict[int, int], decay: float) -> list[int]:
    """Re-rank fused results with a mild preference for newer filings: each step back in
    the company's filing history multiplies the rank score by `decay`."""
    score = {i: decay ** ages.get(i, 0) / (RRF_K + rank) for rank, i in enumerate(ids, 1)}
    return sorted(ids, key=lambda i: -score[i])


def filing_ages(conn: psycopg.Connection[tuple[Any, ...]], ids: list[int]) -> dict[int, int]:
    """Chunk id -> how many filings of the same company are newer than its filing."""
    rows = conn.execute(
        """
        WITH f AS (
            SELECT accession,
                   dense_rank() OVER (PARTITION BY cik ORDER BY period_end DESC NULLS LAST) - 1
                       AS age
            FROM filings
        )
        SELECT c.id, f.age FROM chunks c
        JOIN sections s ON s.id = c.section_id JOIN f ON f.accession = s.accession
        WHERE c.id = ANY(%s)
        """,
        (ids,),
    ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


class QuestionPart(BaseModel):
    """One independently answerable part of a question: one filing or one company."""

    label: str
    filters: Filters


def balance_groups(filters: Filters) -> list[Filters]:
    """One search per named filing when the question names several, so a long or
    keyword-dense filing cannot crowd the others out of the top k."""
    if filters.accessions and len(filters.accessions) > 1:
        return [filters.model_copy(update={"accessions": [a]}) for a in filters.accessions]
    return [filters]


def interleave(rankings: Sequence[Sequence[int]]) -> list[int]:
    """Round-robin merge: first of each ranking, then second of each, without repeats."""
    merged: dict[int, None] = {}
    for tier in zip_longest(*rankings):
        merged.update((i, None) for i in tier if i is not None)
    return list(merged)


def keyword_tsquery(text: str) -> str | None:
    """OR of the query's words. websearch_to_tsquery ANDs every word, which finds nothing
    for most natural-language questions. ts_rank (not ts_rank_cd) ranks the OR matches: on the
    golden set it lifted keyword Recall@10 from 0.58 to 0.68 and hybrid MRR from 0.41 to 0.48."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return " | ".join(dict.fromkeys(words)) or None


def _where(filters: Filters) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for value, clause in (
        (filters.tickers and [t.upper() for t in filters.tickers], "co.ticker = ANY(%s)"),
        (filters.forms, "f.form = ANY(%s)"),
        (filters.period_from, "f.period_end >= %s"),
        (filters.period_to, "f.period_end <= %s"),
        (filters.sections and [s.upper() for s in filters.sections], "upper(s.item) = ANY(%s)"),
        (filters.accessions, "f.accession = ANY(%s)"),
    ):
        if value:
            clauses.append(clause)
            params.append(value)
    return (" AND ".join(clauses) or "TRUE"), params


def _passage(row: Sequence[Any], score: float = 0.0) -> Passage:
    keys = ("chunk_id", "ticker", "company", "form", "accession", "period_end", "filed_at",
            "section", "section_title", "start_char", "end_char", "text", "source_url",
            "fiscal_year", "fiscal_period")  # fmt: skip
    return Passage(**dict(zip(keys, row, strict=True)), score=score)


class Retriever:
    def __init__(
        self,
        conn: psycopg.Connection[tuple[Any, ...]],
        embedder: Embedder,
        candidates: int = 50,
        mode: SearchMode = "hybrid",
        scope_from_query: bool = True,
        recency_decay: float = RECENCY_DECAY,
    ) -> None:
        self.conn = conn
        self.embedder = embedder
        self.candidates = candidates
        self.mode = mode
        self.scope_from_query = scope_from_query
        self.recency_decay = recency_decay

    def scoped(self, query: str, filters: Filters) -> Filters | None:
        """Restrict to the filings the query names ("most recent 10-Q", "fiscal 2025 10-K")
        and the sections it names ("risk factors"), unless the caller already chose them.

        Returns None when the query names an explicit period or fiscal year that no indexed
        filing has: answering from other periods would be answering a different question.
        Looser references that match nothing leave the filters unchanged."""
        if not self.scope_from_query or filters.accessions:
            return filters
        sections = section_intent(query)
        if sections and not filters.sections:
            filters = filters.model_copy(update={"sections": sections})
        refs = parse_references(query)
        if not refs:
            return filters
        accessions = resolve_accessions(self.conn, refs, filters.tickers, filters.forms,
                                        filters.period_from, filters.period_to)  # fmt: skip
        if accessions:
            return filters.model_copy(update={"accessions": accessions})
        if any(r.period_end or r.fiscal_years for r in refs):
            return None
        return filters

    def parts(self, query: str, filters: Filters | None = None) -> list[QuestionPart]:
        """A question that names several filings has one part per filing; one about several
        companies that names no filing has one part per company. Empty otherwise."""
        scoped = self.scoped(query, filters or Filters())
        if scoped is None:
            return []
        if scoped.accessions and len(scoped.accessions) > 1:
            rows = self.conn.execute(
                """
                SELECT f.accession, co.name, f.form, f.period_end FROM filings f
                JOIN companies co ON co.cik = f.cik WHERE f.accession = ANY(%s)
                ORDER BY co.ticker, f.period_end NULLS LAST, f.accession
                """,
                (scoped.accessions,),
            ).fetchall()
            return [
                QuestionPart(
                    label=f"{name}'s {form}" + (
                        f" for the period ended {end:%B} {end.day}, {end.year}" if end else ""
                    ),
                    filters=scoped.model_copy(update={"accessions": [accession]}),
                )
                for accession, name, form, end in rows
            ]  # fmt: skip
        if scoped.tickers and len(scoped.tickers) > 1:
            return [
                QuestionPart(
                    label=BY_TICKER[t.upper()].name if t.upper() in BY_TICKER else t.upper(),
                    filters=scoped.model_copy(update={"tickers": [t]}),
                )
                for t in scoped.tickers
            ]
        return []

    def keyword_ids(self, query: str, filters: Filters) -> list[int]:
        tsquery = keyword_tsquery(query)
        if tsquery is None:
            return []
        where, params = _where(filters)
        rows = self.conn.execute(
            f"""
            SELECT c.id FROM {PASSAGE_FROM}, to_tsquery('english', %s) q
            WHERE c.tsv @@ q AND {where}
            ORDER BY ts_rank(c.tsv, q) DESC, c.id LIMIT %s
            """,
            [tsquery, *params, self.candidates],
        ).fetchall()
        return [r[0] for r in rows]

    def vector_hits(self, vector: np.ndarray, filters: Filters) -> dict[int, float]:
        """Chunk id -> cosine similarity, best first."""
        where, params = _where(filters)
        rows = self.conn.execute(
            f"""
            SELECT c.id, 1 - (e.embedding <=> %s) FROM {PASSAGE_FROM}
            JOIN embeddings e ON e.chunk_id = c.id AND e.model = %s
            WHERE {where}
            ORDER BY 2 DESC, c.id LIMIT %s
            """,
            [vector, self.embedder.model, *params, self.candidates],
        ).fetchall()
        return {r[0]: float(r[1]) for r in rows}

    def _fused(
        self, query: str, vector: np.ndarray | None, filters: Filters
    ) -> tuple[list[int], dict[int, float]]:
        rankings: list[list[int]] = []
        similarities: dict[int, float] = {}
        if self.mode in ("hybrid", "keyword"):
            rankings.append(self.keyword_ids(query, filters))
        if vector is not None:
            similarities = self.vector_hits(vector, filters)
            rankings.append(list(similarities))
        return reciprocal_rank_fusion(rankings), similarities

    def search(self, query: str, filters: Filters | None = None, k: int = 8) -> list[Passage]:
        """Top k by reciprocal rank fusion. `similarity` is set for chunks the vector search
        found; the best similarity is the abstention signal. Empty when the query names a
        period that is not indexed."""
        scoped = self.scoped(query, filters or Filters())
        if scoped is None:
            return []
        vector = self.embedder.embed_query(query) if self.mode != "keyword" else None
        similarities: dict[int, float] = {}
        rankings = []
        for group in balance_groups(scoped):
            ids, sims = self._fused(query, vector, group)
            rankings.append(ids)
            similarities |= sims
        ids = interleave(rankings)
        if self.recency_decay < 1 and not (
            scoped.accessions or scoped.period_from or scoped.period_to
        ):
            ids = prefer_recent(ids, filing_ages(self.conn, ids), self.recency_decay)
        ids = ids[:k]
        by_id = {p.chunk_id: p for p in passages_by_id(self.conn, ids)}
        return [
            by_id[i].model_copy(
                update={"score": round(1.0 / (RRF_K + rank), 6), "similarity": similarities.get(i)}
            )
            for rank, i in enumerate(ids, start=1)
        ]


def passages_by_id(conn: psycopg.Connection[tuple[Any, ...]], ids: list[int]) -> list[Passage]:
    rows = conn.execute(
        f"SELECT {PASSAGE_COLUMNS} FROM {PASSAGE_FROM} WHERE c.id = ANY(%s)", [ids]
    ).fetchall()
    return [_passage(r) for r in rows]


class PassageWithContext(BaseModel):
    passage: Passage
    before: str
    after: str


def get_passage(
    conn: psycopg.Connection[tuple[Any, ...]],
    chunk_id: int | None = None,
    accession: str | None = None,
    section: str | None = None,
    start_char: int | None = None,
    end_char: int | None = None,
    context_chars: int = 600,
) -> PassageWithContext | None:
    """A chunk by id, or an exact span of one section: offsets index into the section text,
    and every section starts at 0, so accession, section and start_char are all required.
    Without end_char the span runs to the end of the chunk containing start_char."""
    params: list[Any]
    if chunk_id is not None:
        where, params = "c.id = %s", [chunk_id]
    elif accession is not None and section is not None and start_char is not None:
        if start_char < 0 or (end_char is not None and end_char <= start_char):
            raise ValueError("need 0 <= start_char < end_char")
        if end_char is not None and end_char - start_char > MAX_SPAN_CHARS:
            raise ValueError(f"a span can be at most {MAX_SPAN_CHARS} characters")
        where = (
            "f.accession = %s AND upper(s.item) = upper(%s)"
            " AND c.start_char <= %s AND c.end_char > %s"
        )
        params = [accession, section, start_char, start_char]
    else:
        raise ValueError("give chunk_id, or accession, section and start_char")
    row = conn.execute(
        f"SELECT {PASSAGE_COLUMNS}, s.text FROM {PASSAGE_FROM} WHERE {where}"
        " ORDER BY c.start_char LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    passage, section_text = _passage(row[:-1]), str(row[-1])
    if chunk_id is None and start_char is not None:
        end = min(end_char if end_char is not None else passage.end_char, len(section_text))
        passage = passage.model_copy(
            update={"start_char": start_char, "end_char": end, "text": section_text[start_char:end]}
        )
    return PassageWithContext(
        passage=passage,
        before=section_text[max(0, passage.start_char - context_chars) : passage.start_char],
        after=section_text[passage.end_char : passage.end_char + context_chars],
    )


class FilingSummary(BaseModel):
    accession: str
    form: Form
    period_end: date | None
    filed_at: date
    source_url: str


def latest_filings(
    conn: psycopg.Connection[tuple[Any, ...]], ticker: str, form: Form, limit: int = 2
) -> list[FilingSummary]:
    rows = conn.execute(
        """
        SELECT f.accession, f.form, f.period_end, f.filed_at, f.source_url
        FROM filings f JOIN companies co ON co.cik = f.cik
        WHERE co.ticker = %s AND f.form = %s
        ORDER BY f.period_end DESC NULLS LAST, f.filed_at DESC LIMIT %s
        """,
        (ticker.upper(), form, limit),
    ).fetchall()
    return [
        FilingSummary(accession=r[0], form=r[1], period_end=r[2], filed_at=r[3], source_url=r[4])
        for r in rows
    ]


class CoverageRow(BaseModel):
    ticker: str
    company: str
    filer_type: str
    forms: dict[str, int]
    earliest_period: date | None
    latest_period: date | None
    latest_filed: date | None = None


def coverage(conn: psycopg.Connection[tuple[Any, ...]]) -> list[CoverageRow]:
    rows = conn.execute(
        """
        SELECT co.ticker, co.name, co.filer_type,
               count(*) FILTER (WHERE f.form = '10-K'), count(*) FILTER (WHERE f.form = '10-Q'),
               min(f.period_end), max(f.period_end), max(f.filed_at),
               count(*) FILTER (WHERE f.form = '20-F')
        FROM companies co LEFT JOIN filings f ON f.cik = co.cik
        GROUP BY co.ticker, co.name, co.filer_type ORDER BY co.ticker
        """
    ).fetchall()
    return [
        CoverageRow(ticker=r[0], company=r[1], filer_type=r[2],
                    forms={"10-K": r[3], "10-Q": r[4], **({"20-F": r[8]} if r[8] else {})},
                    earliest_period=r[5], latest_period=r[6],
                    latest_filed=r[7])
        for r in rows
    ]  # fmt: skip


class IndexRun(BaseModel):
    command: str
    finished_at: datetime
    filings_indexed: int
    failures: int


def last_run(conn: psycopg.Connection[tuple[Any, ...]]) -> IndexRun | None:
    """The most recent ingest or update run: when EDGAR was last checked."""
    row = conn.execute(
        "SELECT command, finished_at, filings_indexed, failures FROM index_runs"
        " ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    return None if row is None else IndexRun(command=row[0], finished_at=row[1],
                                             filings_indexed=row[2], failures=row[3])  # fmt: skip


class ChangedPassage(BaseModel):
    newer: Passage
    closest_older: Passage | None
    similarity: float


def changed_passages(
    conn: psycopg.Connection[tuple[Any, ...]],
    model: str,
    older_accession: str,
    newer_accession: str,
    older_section: str,
    newer_section: str,
    k: int,
) -> list[ChangedPassage]:
    """Newer chunks whose closest older chunk in the matching section is least similar:
    new or rewritten language. The sections differ when the forms do (10-K 1A, 10-Q II-1A).
    Uses the stored embeddings, so no model call."""
    rows = conn.execute(
        """
        WITH side AS (
            SELECT c.id, s.accession, e.embedding FROM chunks c
            JOIN sections s ON s.id = c.section_id
            JOIN embeddings e ON e.chunk_id = c.id AND e.model = %(model)s
            WHERE (s.accession = %(older)s AND upper(s.item) = upper(%(older_section)s))
               OR (s.accession = %(newer)s AND upper(s.item) = upper(%(newer_section)s))
        )
        SELECT n.id, best.id, best.similarity FROM side n
        LEFT JOIN LATERAL (
            SELECT o.id, 1 - (o.embedding <=> n.embedding) AS similarity FROM side o
            WHERE o.accession = %(older)s ORDER BY o.embedding <=> n.embedding LIMIT 1
        ) best ON TRUE
        WHERE n.accession = %(newer)s
        ORDER BY best.similarity ASC NULLS FIRST, n.id LIMIT %(k)s
        """,
        {"model": model, "older_section": older_section, "newer_section": newer_section,
         "older": older_accession, "newer": newer_accession, "k": k},
    ).fetchall()  # fmt: skip
    ids = [r[0] for r in rows] + [r[1] for r in rows if r[1] is not None]
    by_id = {p.chunk_id: p for p in passages_by_id(conn, ids)}
    return [
        ChangedPassage(
            newer=by_id[newer_id],
            closest_older=by_id.get(older_id) if older_id is not None else None,
            similarity=round(float(similarity), 4) if similarity is not None else 0.0,
        )
        for newer_id, older_id, similarity in rows
    ]


def embedding_coverage(conn: psycopg.Connection[tuple[Any, ...]], model: str) -> tuple[int, int]:
    """(chunks with a vector for this model, all chunks)."""
    row = conn.execute(
        "SELECT count(e.chunk_id), count(*) FROM chunks c"
        " LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model = %s",
        (model,),
    ).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def embedding_note(model: str, embedded: int, chunks: int) -> str | None:
    """Semantic search only sees chunks with a vector; say so when some have none."""
    if embedded >= chunks:
        return None
    if embedded == 0:
        return (f"No chunk has a {model} vector, so search is keyword-only and similarity "
                "scores are missing. Run: filings embed")  # fmt: skip
    return (f"{chunks - embedded} of {chunks} chunks have no {model} vector; semantic search "
            "skips them and finds them by keyword only. Run: filings embed")  # fmt: skip
