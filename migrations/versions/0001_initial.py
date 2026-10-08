"""Initial schema: companies, filings, sections, chunks, embeddings.

Embeddings live in their own table keyed by (chunk_id, model) so several embedding models
can be compared on the same chunks. The vector column has no fixed dimension, so search is
an exact scan; at tens of thousands of chunks that is fast enough without an ANN index.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE companies (
            cik integer PRIMARY KEY,
            ticker text NOT NULL UNIQUE,
            name text NOT NULL,
            tier smallint NOT NULL,
            filer_type text NOT NULL CHECK (filer_type IN ('domestic', 'foreign'))
        );

        CREATE TABLE filings (
            accession text PRIMARY KEY,
            cik integer NOT NULL REFERENCES companies (cik),
            form text NOT NULL CHECK (form IN ('10-K', '10-Q')),
            period_end date,
            filed_at date NOT NULL,
            source_url text NOT NULL,
            raw_path text NOT NULL,
            parser_version text NOT NULL,
            missing_sections text[] NOT NULL DEFAULT '{}',
            indexed_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX filings_cik_form_period ON filings (cik, form, period_end);

        CREATE TABLE sections (
            id bigserial PRIMARY KEY,
            accession text NOT NULL REFERENCES filings (accession) ON DELETE CASCADE,
            item text NOT NULL,
            title text NOT NULL,
            text text NOT NULL,
            UNIQUE (accession, item)
        );

        CREATE TABLE chunks (
            id bigserial PRIMARY KEY,
            section_id bigint NOT NULL REFERENCES sections (id) ON DELETE CASCADE,
            ordinal integer NOT NULL,
            start_char integer NOT NULL,
            end_char integer NOT NULL,
            text text NOT NULL,
            has_table boolean NOT NULL,
            tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
            UNIQUE (section_id, ordinal),
            CHECK (start_char < end_char)
        );
        CREATE INDEX chunks_tsv ON chunks USING gin (tsv);

        CREATE TABLE embeddings (
            chunk_id bigint NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
            model text NOT NULL,
            embedding vector NOT NULL,
            PRIMARY KEY (chunk_id, model)
        );
        CREATE INDEX embeddings_model ON embeddings (model);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE embeddings, chunks, sections, filings, companies")
