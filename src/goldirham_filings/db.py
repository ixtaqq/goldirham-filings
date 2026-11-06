from typing import Any

import psycopg
from pgvector.psycopg import register_vector

from goldirham_filings.config import settings
from goldirham_filings.errors import DependencyError, database_errors


def connect(url: str | None = None) -> psycopg.Connection[tuple[Any, ...]]:
    url = url or settings().database_url
    with database_errors(url):
        conn = psycopg.connect(url, connect_timeout=10)
    try:
        register_vector(conn)
    except psycopg.ProgrammingError as error:
        conn.close()
        raise DependencyError(
            "The database has no pgvector extension yet. "
            r"Run: .venv\Scripts\alembic upgrade head"
        ) from error
    return conn
