"""Local embeddings through Ollama. Each model's query/document prefixes live in one table."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import numpy as np
import psycopg

from goldirham_filings.errors import DependencyError, ollama_error


@dataclass(frozen=True)
class EmbeddingModel:
    name: str
    document_prefix: str = ""
    query_prefix: str = ""


EMBEDDING_MODELS = {
    m.name: m
    for m in (
        EmbeddingModel("nomic-embed-text", "search_document: ", "search_query: "),
        EmbeddingModel(
            "mxbai-embed-large", "", "Represent this sentence for searching relevant passages: "
        ),
        EmbeddingModel("bge-m3"),
    )
}


class Embedder(Protocol):
    model: str

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class OllamaEmbedder:
    def __init__(
        self,
        model: str,
        base_url: str,
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if model not in EMBEDDING_MODELS:
            raise DependencyError(
                f"Embedding model {model} is not supported. "
                f"Set EMBEDDING_MODEL to one of: {', '.join(sorted(EMBEDDING_MODELS))}."
            )
        self.model = model
        self._spec = EMBEDDING_MODELS[model]
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def _embed(self, inputs: list[str]) -> list[np.ndarray]:
        try:
            response = self._http.post(
                "/api/embed", json={"model": self.model, "input": inputs, "truncate": True}
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ollama_error(error, str(self._http.base_url).rstrip("/"), self.model) from error
        return [np.asarray(v, dtype=np.float32) for v in response.json()["embeddings"]]

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        return self._embed([self._spec.document_prefix + t for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([self._spec.query_prefix + text])[0]


def embed_missing(
    conn: psycopg.Connection[tuple[Any, ...]], embedder: Embedder, batch_size: int = 32
) -> int:
    """Embed every chunk that has no vector for this model yet. Safe to rerun."""
    rows = conn.execute(
        """
        SELECT c.id, c.text FROM chunks c
        WHERE NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.chunk_id = c.id AND e.model = %s)
        ORDER BY c.id
        """,
        (embedder.model,),
    ).fetchall()
    done = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vectors = embedder.embed_documents([str(text) for _, text in batch])
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO embeddings (chunk_id, model, embedding) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [
                    (chunk_id, embedder.model, v)
                    for (chunk_id, _), v in zip(batch, vectors, strict=True)
                ],
            )
        conn.commit()
        done += len(batch)
        if done % 1024 < batch_size:
            print(f"embedded {done}/{len(rows)} with {embedder.model}")
    return done
