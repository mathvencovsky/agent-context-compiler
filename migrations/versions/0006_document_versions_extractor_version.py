"""document_versions.extractor_version (+ the headers extraction reads)

Revision ID: 0006_versions_extractor_version
Revises: 0005_chunks_embedding_model
Create Date: 2026-10-29 00:00:00.000000

A version is now one (source, raw bytes, extractor version): after an extractor
upgrade, unchanged bytes are re-extracted from the stored raw body under a new
``version_id`` instead of serving the old extraction forever (DECISIONS.md
2026-10-29, SPEC §4/§5/§7).

* ``extractor_version`` records the ``EXTRACTOR_VERSION`` that built the IR.
  Existing rows get NULL, which never matches, so they count as outdated and are
  re-extracted once; their rows, blobs and chunks are left untouched.
* ``content_type_header`` / ``link_header`` keep the response headers the
  extractors read (charset, Markdown alternate), so a re-extraction from the
  stored bytes decodes them exactly as the original fetch did. NULL on existing
  rows (a re-extraction then sniffs the charset from the body).
* The ``(source_id, content_hash)`` unique constraint becomes
  ``(source_id, content_hash, extractor_version)``: two extractions of the same
  bytes are two versions.

Downgrade restores the old constraint, which fails if a source already holds two
versions of the same bytes (delete the newer ones first); it never deletes data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_versions_extractor_version"
down_revision: str | None = "0005_chunks_embedding_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Postgres' default name for the unnamed constraint created by 0002.
_OLD_UNIQUE = "document_versions_source_id_content_hash_key"
_NEW_UNIQUE = "uq_document_versions_source_hash_extractor"


def upgrade() -> None:
    op.add_column(
        "document_versions", sa.Column("extractor_version", sa.Text(), nullable=True)
    )
    op.add_column(
        "document_versions",
        sa.Column("content_type_header", sa.Text(), nullable=True),
    )
    op.add_column(
        "document_versions", sa.Column("link_header", sa.Text(), nullable=True)
    )
    op.drop_constraint(_OLD_UNIQUE, "document_versions", type_="unique")
    op.create_unique_constraint(
        _NEW_UNIQUE,
        "document_versions",
        ["source_id", "content_hash", "extractor_version"],
    )


def downgrade() -> None:
    op.drop_constraint(_NEW_UNIQUE, "document_versions", type_="unique")
    op.create_unique_constraint(
        _OLD_UNIQUE, "document_versions", ["source_id", "content_hash"]
    )
    op.drop_column("document_versions", "link_header")
    op.drop_column("document_versions", "content_type_header")
    op.drop_column("document_versions", "extractor_version")
