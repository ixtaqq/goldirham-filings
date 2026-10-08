from typing import Any

import psycopg
from pgvector.psycopg import register_vector

from goldirham_filings.config import settings


def connect(url: str | None = None) -> psycopg.Connection[tuple[Any, ...]]:
    conn = psycopg.connect(url or settings().database_url)
    register_vector(conn)
    return conn
