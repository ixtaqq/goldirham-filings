"""Each ingest or update run is recorded, so coverage can say when EDGAR was last checked
even when a run found nothing new.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE index_runs (
            id bigserial PRIMARY KEY,
            command text NOT NULL CHECK (command IN ('ingest', 'update')),
            started_at timestamptz NOT NULL,
            finished_at timestamptz NOT NULL DEFAULT now(),
            filings_indexed integer NOT NULL,
            failures integer NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE index_runs")
