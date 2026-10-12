"""chunks

Revision ID: 0003_chunks
Revises: 0002_sources_document_versions
Create Date: 2026-10-12 00:00:00.000000

Creates the M6 ``chunks`` table (SPEC §5, §7) backing lexical + vector search.

The plain columns are created with ``op.create_table``; the two
Postgres-specific columns are added with raw SQL via ``op.execute``:

- ``tsv`` is a GENERATED column (``to_tsvector('simple', text)`` STORED) — the
  app never writes it, Postgres keeps it in sync with ``text``.
- ``embedding`` is a pgvector ``vector(1536)`` column; the ``vector`` extension
  is already enabled by revision ``0001_enable_vector``. 1536 is EMBEDDING_DIM
  (DECISIONS.md 2026-10-12 — text-embedding-3-small produces 1536-dim vectors).

Two indexes back retrieval (M7): a GIN index on ``tsv`` (lexical) and an HNSW
index on ``embedding`` with ``vector_cosine_ops`` (vector similarity).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_chunks"
down_revision: str | None = "0002_sources_document_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: EMBEDDING_DIM — the pgvector column dimensionality (DECISIONS.md 2026-10-12).
_EMBEDDING_DIM = 1536

_TSV_INDEX = "ix_chunks_tsv_gin"
_EMBEDDING_INDEX = "ix_chunks_embedding_hnsw"


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("block_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("heading_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("md", sa.String(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"]),
    )
    op.create_index("ix_chunks_version_id", "chunks", ["version_id"])

    # Postgres-specific columns (raw SQL): the generated tsvector and the
    # pgvector embedding column (the 'vector' extension is enabled by 0001).
    op.execute(
        "ALTER TABLE chunks ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED"
    )
    op.execute(f"ALTER TABLE chunks ADD COLUMN embedding vector({_EMBEDDING_DIM})")

    # Lexical (GIN on tsv) and vector (HNSW cosine) indexes for M7 retrieval.
    op.execute(f"CREATE INDEX {_TSV_INDEX} ON chunks USING gin (tsv)")
    op.execute(
        f"CREATE INDEX {_EMBEDDING_INDEX} ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_EMBEDDING_INDEX}")
    op.execute(f"DROP INDEX IF EXISTS {_TSV_INDEX}")
    op.drop_index("ix_chunks_version_id", table_name="chunks")
    op.drop_table("chunks")
