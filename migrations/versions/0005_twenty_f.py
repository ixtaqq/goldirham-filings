"""Filings and XBRL facts may be 20-F, for foreign private issuers.

Widening a CHECK: every existing row already satisfies the new constraint. The downgrade
fails while any 20-F row exists; remove those filings first.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"


def _set_forms(forms: str) -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in ("filings", "xbrl_facts"):
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT {table}_form_check, "
            f"ADD CONSTRAINT {table}_form_check CHECK (form IN ({forms}))"
        )


def upgrade() -> None:
    _set_forms("'10-K', '10-Q', '20-F'")


def downgrade() -> None:
    _set_forms("'10-K', '10-Q'")
