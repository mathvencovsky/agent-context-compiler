"""usage_events.api_key_id nullable (local use)

Revision ID: 0008_usage_events_local
Revises: 0007_versions_tokens_raw_html
Create Date: 2026-11-02 00:00:00.000000

Local use (ENV=dev) has no API key, so it left no usage record and real use of
NINA could not be measured (DECISIONS 2026-11-02). With ``record_local_usage``
on, those requests are recorded with ``api_key_id`` NULL. Keyed rows and the
foreign key are unchanged.

Downgrade deletes the keyless (local) rows first, since NOT NULL cannot hold
them, then restores the constraint; keyed rows are never touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_usage_events_local"
down_revision: str | None = "0007_versions_tokens_raw_html"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "usage_events", "api_key_id", existing_type=sa.String(), nullable=True
    )


def downgrade() -> None:
    op.execute("DELETE FROM usage_events WHERE api_key_id IS NULL")
    op.alter_column(
        "usage_events", "api_key_id", existing_type=sa.String(), nullable=False
    )
