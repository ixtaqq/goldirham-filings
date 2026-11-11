import psycopg
import pytest

from goldirham_filings.db import connect


@pytest.mark.db
def test_read_only_connection_refuses_writes(database_url: str) -> None:
    with (
        connect(database_url, read_only=True) as conn,
        pytest.raises(psycopg.errors.ReadOnlySqlTransaction),
    ):
        conn.execute(
            "INSERT INTO index_runs (command, started_at, filings_indexed, failures)"
            " VALUES ('update', now(), 0, 0)"
        )
