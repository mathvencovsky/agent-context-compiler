"""sources + document_versions

Revision ID: 0002_sources_document_versions
Revises: 0001_enable_vector
Create Date: 2026-10-11 00:00:00.000000

Creates the two M5 tables (SPEC §7). ``sources`` and ``document_versions``
reference each other, so the circular FK ``sources.current_version_id ->
document_versions.id`` is added with a deferred ``ALTER TABLE`` after both
tables exist, and dropped first on downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_sources_document_versions"
down_revision: str | None = "0001_enable_vector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURRENT_VERSION_FK = "fk_sources_current_version_id_document_versions"


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("url_normalized", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("current_version_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url_normalized"),
    )
    op.create_index("ix_sources_url_normalized", "sources", ["url_normalized"])
    op.create_index("ix_sources_domain", "sources", ["domain"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("etag", sa.String(), nullable=True),
        sa.Column("last_modified", sa.String(), nullable=True),
        sa.Column("render_mode", sa.String(), nullable=False),
        sa.Column("raw_blob_key", sa.String(), nullable=False),
        sa.Column("ir_blob_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.UniqueConstraint("source_id", "content_hash"),
    )
    op.create_index(
        "ix_document_versions_source_id", "document_versions", ["source_id"]
    )
    op.create_index(
        "ix_document_versions_content_hash",
        "document_versions",
        ["content_hash"],
    )

    # Circular FK added after both tables exist (deferred ALTER TABLE).
    op.create_foreign_key(
        _CURRENT_VERSION_FK,
        "sources",
        "document_versions",
        ["current_version_id"],
        ["id"],
    )


def downgrade() -> None:
    # Drop the circular FK before the tables it ties together.
    op.drop_constraint(_CURRENT_VERSION_FK, "sources", type_="foreignkey")

    op.drop_index("ix_document_versions_content_hash", table_name="document_versions")
    op.drop_index("ix_document_versions_source_id", table_name="document_versions")
    op.drop_table("document_versions")

    op.drop_index("ix_sources_domain", table_name="sources")
    op.drop_index("ix_sources_url_normalized", table_name="sources")
    op.drop_table("sources")
