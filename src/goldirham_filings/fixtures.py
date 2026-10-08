"""Committed fixture corpus for the CI retrieval gate.

CI has no GPU and no Ollama, so the fixture carries precomputed vectors: one per chunk and
one per golden question, all from a single embedding model named in the manifest. Chunk
text is not stored; it is the section text sliced by the chunk's offsets.
"""

import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np
import psycopg

from goldirham_filings.ingest import upsert_companies

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "evals" / "fixtures"


def export(
    conn: psycopg.Connection[tuple[Any, ...]],
    accessions: list[str],
    model: str,
    query_vectors: dict[str, np.ndarray],
    out_dir: Path = FIXTURE_DIR,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    filings = []
    vectors: list[np.ndarray] = []
    for accession in accessions:
        f = conn.execute(
            "SELECT accession, cik, form, period_end, filed_at, source_url, parser_version,"
            " fiscal_year, fiscal_period"
            " FROM filings WHERE accession = %s",
            (accession,),
        ).fetchone()
        if f is None:
            raise ValueError(f"{accession} is not indexed")
        sections = []
        for section_id, item, title, text in conn.execute(
            "SELECT id, item, title, text FROM sections WHERE accession = %s ORDER BY id",
            (accession,),
        ).fetchall():
            chunks = conn.execute(
                """
                SELECT c.ordinal, c.start_char, c.end_char, c.has_table, e.embedding
                FROM chunks c JOIN embeddings e ON e.chunk_id = c.id AND e.model = %s
                WHERE c.section_id = %s ORDER BY c.ordinal
                """,
                (model, section_id),
            ).fetchall()
            sections.append({"item": item, "title": title, "text": text,
                             "chunks": [list(c[:4]) for c in chunks]})  # fmt: skip
            vectors.extend(np.asarray(c[4].to_numpy(), dtype=np.float32) for c in chunks)
        filings.append({
            "accession": f[0], "cik": f[1], "form": f[2], "period_end": f[3] and f[3].isoformat(),
            "filed_at": str(f[4]), "source_url": f[5], "parser_version": f[6],
            "fiscal_year": f[7], "fiscal_period": f[8],
            "sections": sections,
        })  # fmt: skip
    with gzip.open(out_dir / "corpus.jsonl.gz", "wt", encoding="utf-8") as fh:
        for filing in filings:
            fh.write(json.dumps(filing) + "\n")
    np.save(out_dir / "chunk_vectors.npy", np.stack(vectors).astype(np.float16))
    questions = sorted(query_vectors)
    np.save(out_dir / "query_vectors.npy",
            np.stack([query_vectors[q] for q in questions]).astype(np.float16))  # fmt: skip
    manifest = {"embedding_model": model, "accessions": accessions, "questions": questions,
                "chunks": len(vectors)}  # fmt: skip
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load(conn: psycopg.Connection[tuple[Any, ...]], fixture_dir: Path = FIXTURE_DIR) -> int:
    """Load the fixture into an empty, migrated database. Returns the chunk count."""
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    vectors = np.load(fixture_dir / "chunk_vectors.npy").astype(np.float32)
    upsert_companies(conn)
    n = 0
    with (
        gzip.open(fixture_dir / "corpus.jsonl.gz", "rt", encoding="utf-8") as fh,
        conn.transaction(),
    ):
        for line in fh:
            f = json.loads(line)
            conn.execute(
                "INSERT INTO filings (accession, cik, form, period_end, filed_at, source_url,"
                " raw_path, parser_version, fiscal_year, fiscal_period)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'fixture', %s, %s, %s)",
                (f["accession"], f["cik"], f["form"], f["period_end"], f["filed_at"],
                 f["source_url"], f["parser_version"], f["fiscal_year"], f["fiscal_period"]),
            )  # fmt: skip
            for s in f["sections"]:
                row = conn.execute(
                    "INSERT INTO sections (accession, item, title, text) VALUES (%s, %s, %s, %s)"
                    " RETURNING id",
                    (f["accession"], s["item"], s["title"], s["text"]),
                ).fetchone()
                assert row is not None
                for ordinal, start, end, has_table in s["chunks"]:
                    chunk = conn.execute(
                        "INSERT INTO chunks (section_id, ordinal, start_char, end_char, text,"
                        " has_table) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                        (row[0], ordinal, start, end, s["text"][start:end], has_table),
                    ).fetchone()
                    assert chunk is not None
                    conn.execute(
                        "INSERT INTO embeddings (chunk_id, model, embedding) VALUES (%s, %s, %s)",
                        (chunk[0], manifest["embedding_model"], vectors[n]),
                    )
                    n += 1
        if n != len(vectors) or n != manifest["chunks"]:
            raise ValueError(
                f"fixture has {n} chunks, {len(vectors)} vectors, {manifest['chunks']} in manifest"
            )
    return n


class FixtureEmbedder:
    """Serves the precomputed golden-question vectors; any other query is an error."""

    def __init__(self, fixture_dir: Path = FIXTURE_DIR) -> None:
        manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
        self.model: str = manifest["embedding_model"]
        vectors = np.load(fixture_dir / "query_vectors.npy").astype(np.float32)
        self._vectors: dict[str, np.ndarray] = dict(
            zip(manifest["questions"], vectors, strict=True)
        )
        self.accessions: list[str] = manifest["accessions"]

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        raise NotImplementedError("fixture vectors are loaded, not computed")

    def embed_query(self, text: str) -> np.ndarray:
        return self._vectors[text]
