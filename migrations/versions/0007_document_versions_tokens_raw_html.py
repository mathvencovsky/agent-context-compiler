"""document_versions.tokens_raw_html

Revision ID: 0007_versions_tokens_raw_html
Revises: 0006_versions_extractor_version
Create Date: 2026-10-29 00:00:00.000000

Every response served from a stored version (cache hit, 304, same-hash dedup,
GET /v1/documents/{id}) reported ``stats.tokens_raw_html`` by downloading the
raw body from S3 and tokenizing all of it, ~0.4 s on a 1.2 MB page (research
round 4, K L3). The count depends only on the version's immutable raw bytes, so
it is now computed once when the version is created and stored here (DECISIONS
2026-10-29). NULL on existing rows: those still count from the raw blob, with
the same result as before.

Downgrade drops the column; it never touches any other data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_versions_tokens_raw_html"
down_revision: str | None = "0006_versions_extractor_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("tokens_raw_html", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "tokens_raw_html")
