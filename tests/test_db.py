import os

import psycopg
import pytest

from goldirham_filings.db import connect


@pytest.mark.db
def test_read_only_connection_refuses_writes() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    with connect(url, read_only=True) as conn, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute(
            "INSERT INTO index_runs (command, started_at, filings_indexed, failures)"
            " VALUES ('update', now(), 0, 0)"
        )
