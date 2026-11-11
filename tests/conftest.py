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
def database_url() -> str:
    """The fixture corpus database (scripts/check.ps1 and CI set TEST_DATABASE_URL and run
    `filings fixtures load`). Skipped locally without it; a failure in CI, where a silent
    skip would pass the build without running the database tests."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    if os.environ.get("CI"):
        pytest.fail("TEST_DATABASE_URL is not set in CI")
    pytest.skip("TEST_DATABASE_URL is not set")


@pytest.fixture
def fixture_conn(database_url: str) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
    with connect(database_url) as conn:
        yield conn
