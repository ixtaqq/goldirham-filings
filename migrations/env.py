import os

from alembic import context
from sqlalchemy import create_engine

from goldirham_filings.config import settings


def sqlalchemy_url() -> str:
    url = os.environ.get("DATABASE_URL") or settings().database_url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


with create_engine(sqlalchemy_url()).connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
