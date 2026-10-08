"""Filings record their own fiscal year and period from the cover-page DEI tags.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("ALTER TABLE filings ADD COLUMN fiscal_year smallint, ADD COLUMN fiscal_period text")


def downgrade() -> None:
    op.execute("ALTER TABLE filings DROP COLUMN fiscal_year, DROP COLUMN fiscal_period")
