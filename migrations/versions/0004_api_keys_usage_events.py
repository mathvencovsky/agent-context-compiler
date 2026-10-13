"""api_keys + usage_events

Revision ID: 0004_api_keys_usage_events
Revises: 0003_chunks
Create Date: 2026-10-14 00:00:00.000000

Creates the M9 ``api_keys`` and ``usage_events`` tables (SPEC §7).

- ``api_keys`` holds one row per issued key. Only the sha256 ``key_hash`` is
  persisted (the plaintext, prefixed ``nina_``, is shown to the operator ONCE
  by ``app/admin.py`` and never stored — CLAUDE.md: secrets never live in code,
  tests or logs). ``revoked_at`` NULL means active; ``limits`` is a per-key
  JSONB override map (FEAT-003 rate limiting reads it).
- ``usage_events`` records one row per authenticated request. The MODEL + table
  land here (FEAT-001); the WRITING/aggregation of these rows is FEAT-002. The
  composite index ``(api_key_id, ts)`` backs the per-key usage rollups.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_api_keys_usage_events"
down_revision: str | None = "0003_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USAGE_API_KEY_INDEX = "ix_usage_events_api_key_id"
_USAGE_API_KEY_TS_INDEX = "ix_usage_events_api_key_ts"


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "limits",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("api_key_id", sa.String(), nullable=False),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "units",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
    )
    op.create_index(_USAGE_API_KEY_INDEX, "usage_events", ["api_key_id"])
    op.create_index(_USAGE_API_KEY_TS_INDEX, "usage_events", ["api_key_id", "ts"])


def downgrade() -> None:
    # usage_events first (it carries the FK to api_keys).
    op.drop_index(_USAGE_API_KEY_TS_INDEX, table_name="usage_events")
    op.drop_index(_USAGE_API_KEY_INDEX, table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_table("api_keys")
