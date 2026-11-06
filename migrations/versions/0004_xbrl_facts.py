"""XBRL facts from EDGAR companyfacts, for exact reported figures.

Only facts filed in a 10-K or 10-Q are kept. The same figure appears in several filings as a
comparative; every copy is kept with its own accession, and selection happens at read time.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE xbrl_facts (
            cik integer NOT NULL REFERENCES companies (cik),
            taxonomy text NOT NULL,
            concept text NOT NULL,
            label text NOT NULL,
            unit text NOT NULL,
            period_start date,
            period_end date NOT NULL,
            value numeric NOT NULL,
            accession text NOT NULL,
            form text NOT NULL CHECK (form IN ('10-K', '10-Q')),
            fiscal_year smallint,
            fiscal_period text,
            filed date NOT NULL
        );
        CREATE INDEX xbrl_facts_lookup ON xbrl_facts (cik, lower(concept), period_end);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE xbrl_facts")
