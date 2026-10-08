import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from goldirham_filings.db import connect


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fixture_conn() -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
    """Connection to a database loaded with the committed fixture corpus
    (scripts/check.ps1 and CI set TEST_DATABASE_URL and run `filings fixtures load`)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    with connect(url) as conn:
        yield conn
