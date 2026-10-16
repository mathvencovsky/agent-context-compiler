"""chunks.embedding_model

Revision ID: 0005_chunks_embedding_model
Revises: 0004_api_keys_usage_events
Create Date: 2026-10-16 00:00:00.000000

Records which embeddings model produced each chunk's vector, so the indexer can
reuse a version's chunks and vectors across ``assemble`` calls and re-embed only
when the chunk text or the model changed (DECISIONS.md 2026-10-16). Existing
rows get NULL, which never matches a model, so they are re-embedded once.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_chunks_embedding_model"
down_revision: str | None = "0004_api_keys_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("embedding_model", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chunks", "embedding_model")
