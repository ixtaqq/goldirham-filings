"""Failures of the services this tool depends on, as messages that say what to do.

Ollama and Postgres errors are translated where they are raised; the MCP server turns a
DependencyError into a ToolError and the CLI into a message and a non-zero exit.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict


class DependencyError(RuntimeError):
    """A service the tool needs is down, misconfigured or missing data."""


def ollama_error(error: httpx.HTTPError, base_url: str, model: str) -> DependencyError:
    if isinstance(error, httpx.TimeoutException):
        return DependencyError(
            f"Ollama at {base_url} did not respond in time while running {model}. It may be "
            "loading the model or the machine is busy; try again, or check `ollama ps`."
        )
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        if response.status_code == 404 and "not found" in response.text:
            return DependencyError(f"Ollama model {model} is not pulled. Run: ollama pull {model}")
        return DependencyError(
            f"Ollama at {base_url} returned HTTP {response.status_code} for {model}: "
            f"{response.text[:200]}"
        )
    return DependencyError(
        f"Ollama is not reachable at {base_url}. Start Ollama, or install it from ollama.com."
    )


def _where(url: str) -> str:
    try:
        parts = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return "the configured DATABASE_URL"
    return f"{parts.get('host', 'localhost')}:{parts.get('port', 5432)}/{parts.get('dbname', '')}"


def database_error(error: psycopg.Error, url: str) -> DependencyError:
    if isinstance(error, psycopg.errors.UndefinedTable):
        return DependencyError(
            f"The database at {_where(url)} has no filing tables. "
            r"Run: .venv\Scripts\alembic upgrade head, then filings ingest"
        )
    return DependencyError(
        f"The database at {_where(url)} is not reachable. Start Docker Desktop, then run "
        "docker compose up -d --wait in the project folder."
    )


@contextmanager
def database_errors(url: str) -> Iterator[None]:
    """Translate connection loss and a missing schema; other database errors propagate."""
    try:
        yield
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as error:
        raise database_error(error, url) from error
